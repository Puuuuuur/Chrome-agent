# 信用中国 API 化改造方案

本文档用于沉淀当前对 `creditchina` 查询链路的已确认事实、字段映射关系，以及对 `浏览器 Agent` 的后续改造建议。目标是把当前“页面点击流优先”的流程，逐步切换为“浏览器会话内 API 调用流优先，DOM 兜底”。

## 1. 背景与目标

当前真实 `creditchina` 站点在云主机环境下会触发较强的挑战页/反爬机制。即使已经增加了：

- `requests` 预热 Cookie
- 浏览器侧 challenge bootstrap
- 浏览器画像/指纹拟真

仍然会出现：

- 首页 `412 Precondition Failed`
- 后续 `400 + X-Via-JSL + 空白 DOM`

因此，继续单纯强化页面点击流的收益已经有限。更可行的方向是：

1. 继续复用真实浏览器会话，仅负责过挑战和保持动态上下文；
2. 在同一会话内直接调用 `public.creditchina.gov.cn/private-api/...`；
3. 只在 API 流失效或接口结构变化时，才回退到旧 DOM 流。

## 2. 已确认的接口时序

### 2.1 验证码链路

#### 获取验证码图片

- 接口：`GET /private-api/verify/getVerify`
- 域名：`public.creditchina.gov.cn`
- 特征：
  - 响应是图片，不是 JSON
  - `_v` 参数看起来是防缓存随机值
- 用途：
  - 获取图形验证码图片，供 OCR 识别

#### 校验验证码

- 接口：`GET /private-api/verify/checkVerify`
- 请求体：`verifyInput=<验证码>`
- 已确认成功响应：

```json
{
  "msg": "验证成功",
  "code": 0
}
```

- 判定规则：
  - `code == 0` 视为校验成功

### 2.2 搜索结果链路

#### 搜索结果列表

- 接口：`GET /private-api/catalogSearchHome`
- 域名：`public.creditchina.gov.cn`
- 已确认返回结构：

```json
{
  "status": 1,
  "message": "成功",
  "data": {
    "page": 1,
    "total": 50,
    "totalSize": 5,
    "list": [
      {
        "accurate_entity_name": "你好徐州传媒有限公司",
        "accurate_entity_code": "91320322MABLP2X24R",
        "uuid": "84b94add2d2637d5d4e0f4beb8ee5949",
        "recid": "8498C7D7D52F48BDA6DD475FA670F4C1",
        "accurate_entity_name_query": "你好徐州传媒有限公司",
        "entityType": "1"
      }
    ]
  }
}
```

- 判定规则：
  - `status == 1` 视为请求成功
- 当前观察：
  - 该接口依赖当前浏览器会话上下文
  - 抓到的请求中未显式携带最终关键词字段，但返回了当前查询结果
  - 说明它并非简单的无状态公开查询接口，更像“当前会话查询结果读取接口”

### 2.3 详情页与主体详情链路

#### 详情页入口 URL

已确认详情页 URL 形态类似：

```text
https://www.creditchina.gov.cn/xinyongxinxixiangqing/xyDetail.html?searchState=1&entityType=1&keyword=<企业名>&uuid=<搜索结果页uuid>&tyshxydm=<统一社会信用代码>
```

说明详情页会显式暴露：

- `uuid`
- `tyshxydm`
- `entityType`
- `keyword`

#### 主体详情主接口

- 接口：`GET /private-api/getTyshxydmDetailsContent`
- 已确认成功响应结构包含：

```json
{
  "status": 1,
  "message": "成功",
  "data": {
    "punishmentStatus": "no",
    "data": {
      "columnList": ["name", "enttype", "esdate", "dom"],
      "sencesMap": {
        "name": "法定代表人/负责人/执行事务合伙人",
        "enttype": "企业类型",
        "esdate": "成立日期",
        "dom": "住所"
      },
      "dataSource": "市场监督管理总局",
      "data_catalog": "工商存续（企业）",
      "table_name": "credit_scjdglzj_fr_gscxqy",
      "entity": {
        "name": "梅凤霞",
        "enttype": "有限责任公司(自然人独资)",
        "esdate": "2022-05-18",
        "dom": "江苏省徐州市泉山区泰山街道金山东路2号徐州科创创业园B座512房间",
        "uuid": "c1e6fe18f7b24cc94934722dbf60748f",
        "recid": "8498C7D7D52F48BDA6DD475FA670F4C1",
        "regorg": "泉山区市场监督管理局"
      }
    },
    "headEntity": {
      "recid": "8498C7D7D52F48BDA6DD475FA670F4C1",
      "tyshxydm": "91320322MABLP2X24R",
      "entity_type": "1",
      "jgmc": "你好徐州传媒有限公司",
      "record_source": "企业法人",
      "status": "存续"
    },
    "hgData": {
      "entity": {}
    },
    "rewardStatus": "no"
  }
}
```

- 判定规则：
  - `status == 1` 视为成功
- 关键观察：
  - 搜索结果里的 `uuid` 与详情返回中的 `data.entity.uuid` 不一致
  - `recid` 在搜索结果和详情主接口中是一致的
  - 初步判断 `recid` 比 `uuid` 更像稳定关联键

### 2.4 已排除接口

#### `GET /private-api/info`

已抓到返回：

```json
{
  "status": 1,
  "message": "成功",
  "data": null
}
```

结论：

- 接口可访问
- 但它不是当前主体详情主接口
- 更像辅助接口或依赖额外前置上下文

## 3. 字段映射表

### 3.1 搜索结果字段

| 接口 | 字段 | 业务含义 |
| --- | --- | --- |
| `catalogSearchHome` | `accurate_entity_name` | 企业名称 |
| `catalogSearchHome` | `accurate_entity_code` | 统一社会信用代码 |
| `catalogSearchHome` | `uuid` | 搜索结果页主体标识 |
| `catalogSearchHome` | `recid` | 稳定记录标识 |
| `catalogSearchHome` | `entityType` | 主体类型 |

### 3.2 详情主接口字段

| 接口 | 字段 | 业务含义 |
| --- | --- | --- |
| `getTyshxydmDetailsContent` | `data.headEntity.jgmc` | 企业名称 |
| `getTyshxydmDetailsContent` | `data.headEntity.tyshxydm` | 统一社会信用代码 |
| `getTyshxydmDetailsContent` | `data.headEntity.status` | 存续/状态 |
| `getTyshxydmDetailsContent` | `data.headEntity.recid` | 记录标识 |
| `getTyshxydmDetailsContent` | `data.data.entity.name` | 法定代表人/负责人 |
| `getTyshxydmDetailsContent` | `data.data.entity.enttype` | 企业类型 |
| `getTyshxydmDetailsContent` | `data.data.entity.esdate` | 成立日期 |
| `getTyshxydmDetailsContent` | `data.data.entity.dom` | 住所 |
| `getTyshxydmDetailsContent` | `data.data.entity.regorg` | 登记机关 |
| `getTyshxydmDetailsContent` | `data.rewardStatus` | 是否有奖励/守信信息 |
| `getTyshxydmDetailsContent` | `data.punishmentStatus` | 是否有处罚/失信信息 |

## 4. 当前代码里的接入点

### 4.1 调度入口

- `智能体调度.py`
  - 系统提示里已经明确要求：用户要求执行“信用中国”固定查询流程时，优先走内置固定流程。

### 4.2 当前固定查询工具

- `agent工具.py`
  - `run_creditchina_query_and_save`
  - 当前仍以“打开页面 -> 处理挑战 -> 处理验证码 -> 保存结果”为主流程

### 4.3 当前浏览器拟真与挑战处理

- `agent工具.py`
  - 浏览器画像/拟真配置
  - `requests` 预热 Cookie
  - 浏览器侧 challenge bootstrap
  - challenge 续跑逻辑

### 4.4 当前会话与结果落盘

- `智能体配置.py`
  - `SESSION_DIR`
  - `RESULTS_DIR`

## 5. 推荐改造方向

### 5.1 总体原则

把当前流程改成：

- 浏览器负责：
  - 过挑战
  - 生成/维持动态 Cookie
  - 维持 `rcwCQitg` 和当前会话上下文
- API 负责：
  - 拉验证码
  - 校验验证码
  - 获取搜索结果列表
  - 获取详情主内容
  - 后续扩展行政许可、行政处罚、黑名单等专题接口

### 5.2 不推荐的方向

不建议继续把核心业务接口放到独立 `requests` 会话里直接重放，原因是：

- `rcwCQitg` 明显是动态短效参数
- 查询上下文可能存于当前会话或当前页面状态
- 直接离开浏览器上下文重放，成功率会明显下降

优先建议：

- 使用浏览器同会话内请求
- 若 Playwright 版本支持，优先 `page.context.request`
- 否则退回 `page.evaluate(() => fetch(...))`

## 6. V1 最小可行改造方案

### 6.1 新增内部状态对象

建议维护一个内部 `creditchina session state`，至少记录：

- 当前 `keyword`
- 当前 `credit_code`
- 当前候选列表
- 当前选中的 `uuid`
- 当前选中的 `recid`
- 当前 `entityType`
- 当前 `rcwCQitg`
- 最近一次验证码校验结果

### 6.2 新增 helper

建议在 `agent工具.py` 里新增以下 helper：

- `_capture_creditchina_runtime_state_async()`
  - 从当前页面 URL、请求日志或页面上下文中提取：
    - `keyword`
    - `entityType`
    - `uuid`
    - `tyshxydm`
    - `rcwCQitg`

- `_creditchina_api_get_verify_async()`
  - 拉验证码图片
  - 返回图片 bytes / base64 / 保存路径

- `_creditchina_api_check_verify_async()`
  - 发送 `verifyInput`
  - 成功判定：`code == 0`

- `_creditchina_api_catalog_search_async()`
  - 获取候选企业列表
  - 成功判定：`status == 1`

- `_creditchina_pick_candidate()`
  - 选主体优先级：
    1. 统一社会信用代码精确匹配
    2. 企业名称精确匹配
    3. 只有单一候选时自动选中
    4. 多候选时返回候选列表供上层处理

- `_creditchina_api_get_details_async()`
  - 调 `getTyshxydmDetailsContent`
  - 解析企业主内容

### 6.3 新增 API 流工具

建议新增一个新的主工具：

- `run_creditchina_private_api_query_and_save`

建议流程：

1. 确保挑战已过或浏览器会话已可用
2. 调 `getVerify`
3. OCR 识别验证码
4. 调 `checkVerify`
5. 调 `catalogSearchHome`
6. 选中目标候选主体
7. 调 `getTyshxydmDetailsContent`
8. 组装标准化结果
9. 保存到 `RESULTS_DIR`

### 6.4 与旧工具的关系

当前 `run_creditchina_query_and_save` 不建议直接删除。建议改成：

1. 先尝试 API 流
2. 若 API 流失败，再回退旧 DOM 流

这样改动最小，也便于逐步替换。

## 7. 结果结构建议

建议最终统一写出如下结构：

```json
{
  "ok": true,
  "input": {
    "keyword": "",
    "credit_code": ""
  },
  "verify": {
    "ok": true,
    "code": 0,
    "msg": "验证成功"
  },
  "search_result": {
    "total": 0,
    "selected_candidate": {},
    "candidates": []
  },
  "detail": {
    "head_entity": {},
    "basic_entity": {},
    "customs_entity": {}
  },
  "normalized": {
    "enterprise_name": "",
    "credit_code": "",
    "status": "",
    "legal_person": "",
    "enterprise_type": "",
    "establish_date": "",
    "address": "",
    "registration_authority": ""
  },
  "debug": {
    "used_api_flow": true,
    "api_steps": []
  }
}
```

## 8. 安全与脱敏要求

文档与落盘时不要保存这些敏感值原文：

- 完整 Cookie Header
- 完整 `rcwCQitg`
- 原始验证码值

建议：

- Cookie 仅保留名称列表
- `rcwCQitg` 仅保留前后少量字符或长度
- 调试日志只记录接口名、状态码、是否成功

## 9. 当前落地状态与后续建议

截至 2026-03-15，代码里已经完成这些主改造：

1. 已实现 `run_creditchina_private_api_query_and_save`
2. `run_creditchina_query_and_save` 已接入“API 优先，DOM 兜底”
3. 已覆盖验证码、搜索结果、详情主内容三段主链路

后续更值得继续做的是：

1. 继续补抓并接入行政许可、行政处罚、红名单/黑名单等专题接口
2. 进一步压缩 DOM fallback 触发面，尽量让主链路稳定停留在 API 流
3. 持续完善结果落盘时的脱敏规则和诊断信息组织

## 10. 当前结论

当前已确认的真实链路已经足够说明：

- 信用中国的真实业务查询完全可以转成“浏览器会话内 API 调用流”
- 继续只增强 DOM 点击流的收益有限
- 当前最值得做的是继续扩专题接口、强化脱敏与失败诊断，而不是回到纯 DOM 点击流
