# Milvus Deploy

这个目录用于独立部署浏览器 Agent 的 Milvus Standalone。

## 文件

- `docker-compose.yml`
  - Milvus Standalone + etcd + MinIO

## 启动

```bash
docker compose up -d
```

## 健康检查

```bash
curl http://127.0.0.1:19091/healthz
```

返回：

```text
OK
```

## 当前用途

当前这套独立 Milvus 主要给：

- `rag_kb/`
  - 本地 PDF RAG 检索
