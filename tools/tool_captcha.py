#!/usr/bin/env python3
"""验证码生成与识别工具模块。

这个文件既支持本地模板识别，也支持调用模型做 OCR，
主要给信用中国查询链路里的验证码步骤复用。
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .tool_model_client import build_openai_client
from 智能体配置 import DEFAULT_CAPTCHA_OCR_MODEL


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CAPTCHA_LENGTH = 4
IMAGE_WIDTH = 240
IMAGE_HEIGHT = 80
LEFT_MARGIN = 12
TOP_MARGIN = 12
CELL_WIDTH = 36
CELL_HEIGHT = 56
CELL_GAP = 8
FONT_SIZE = 38
FOREGROUND_THRESHOLD = 150
TEMPLATE_SIZE = 32
DEFAULT_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

logger = logging.getLogger(__name__)


def load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """为验证码生成选择一个可用字体；找不到就退回默认字体。"""
    for font_path in DEFAULT_FONT_PATHS:
        path = Path(font_path)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def generate_captcha_text(length: int = CAPTCHA_LENGTH, rng: random.Random | None = None) -> str:
    """随机生成一个验证码答案字符串。"""
    random_source = rng or random.SystemRandom()
    return "".join(random_source.choice(ALPHABET) for _ in range(length))


def _draw_noise(draw: ImageDraw.ImageDraw, rng: random.Random) -> None:
    """在验证码图片上添加简单干扰线和噪点。"""
    for _ in range(3):
        start = (rng.randint(0, IMAGE_WIDTH), rng.randint(0, IMAGE_HEIGHT))
        end = (rng.randint(0, IMAGE_WIDTH), rng.randint(0, IMAGE_HEIGHT))
        color = (
            rng.randint(175, 220),
            rng.randint(175, 220),
            rng.randint(175, 220),
        )
        draw.line((start, end), fill=color, width=1)

    for _ in range(20):
        x = rng.randint(0, IMAGE_WIDTH - 1)
        y = rng.randint(0, IMAGE_HEIGHT - 1)
        color = (
            rng.randint(180, 230),
            rng.randint(180, 230),
            rng.randint(180, 230),
        )
        draw.point((x, y), fill=color)


def render_captcha_image(text: str, rng: random.Random | None = None) -> Image.Image:
    """把验证码文本绘制成图片。"""
    random_source = rng or random.Random()
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), (252, 253, 255))
    draw = ImageDraw.Draw(image)
    font = load_font()

    _draw_noise(draw, random_source)

    for index, char in enumerate(text):
        x = LEFT_MARGIN + index * (CELL_WIDTH + CELL_GAP)
        y = TOP_MARGIN + random_source.randint(-2, 2)
        char_color = (
            random_source.randint(15, 50),
            random_source.randint(40, 85),
            random_source.randint(90, 150),
        )
        draw.text((x + 3, y), char, font=font, fill=char_color)

    return image


def captcha_image_bytes(text: str) -> bytes:
    """直接返回验证码 PNG 的二进制内容。"""
    image = render_captcha_image(text)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _binarize(image: Image.Image) -> np.ndarray:
    """把图片转成二值前景掩码，方便后续模板匹配。"""
    grayscale = image.convert("L")
    pixels = np.array(grayscale)
    return pixels < FOREGROUND_THRESHOLD


def _normalize_binary(binary: np.ndarray) -> np.ndarray:
    """把字符区域裁切并缩放到统一模板尺寸。"""
    ys, xs = np.where(binary)
    if len(xs) == 0 or len(ys) == 0:
        return np.zeros((TEMPLATE_SIZE, TEMPLATE_SIZE), dtype=np.uint8)

    cropped = binary[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    cropped_image = Image.fromarray((cropped.astype(np.uint8) * 255), mode="L")
    cropped_image.thumbnail((TEMPLATE_SIZE - 8, TEMPLATE_SIZE - 8), Image.Resampling.NEAREST)

    canvas = Image.new("L", (TEMPLATE_SIZE, TEMPLATE_SIZE), 0)
    paste_x = (TEMPLATE_SIZE - cropped_image.width) // 2
    paste_y = (TEMPLATE_SIZE - cropped_image.height) // 2
    canvas.paste(cropped_image, (paste_x, paste_y))

    return (np.array(canvas) > 127).astype(np.uint8)


def _render_template_char(char: str) -> np.ndarray:
    """把单个字符渲染成模板，用于本地 OCR 匹配。"""
    image = Image.new("RGB", (CELL_WIDTH, CELL_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    font = load_font()
    draw.text((3, 0), char, font=font, fill=(0, 0, 0))
    return _normalize_binary(_binarize(image))


TEMPLATES = {char: _render_template_char(char) for char in ALPHABET}


def solve_captcha_image_local(image: Image.Image) -> str:
    """使用本地模板匹配识别验证码图片。"""
    binary = _binarize(image)
    prediction: list[str] = []

    for index in range(CAPTCHA_LENGTH):
        x0 = LEFT_MARGIN + index * (CELL_WIDTH + CELL_GAP)
        x1 = x0 + CELL_WIDTH
        y0 = TOP_MARGIN - 2
        y1 = y0 + CELL_HEIGHT
        cell = binary[y0:y1, x0:x1]
        normalized = _normalize_binary(cell)

        best_char = "?"
        best_score = float("inf")
        for char, template in TEMPLATES.items():
            score = np.mean(np.abs(normalized.astype(np.int16) - template.astype(np.int16)))
            if score < best_score:
                best_score = score
                best_char = char
        prediction.append(best_char)

    return "".join(prediction)


def _image_to_data_url(image: Image.Image) -> str:
    """把 PIL 图片编码成 data URL，方便传给多模态模型。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _extract_response_text(response: object) -> str:
    """从 OpenAI Responses API 返回对象中提取纯文本。"""
    output_text = str(getattr(response, "output_text", "") or "").strip()
    if output_text:
        return output_text

    output = getattr(response, "output", None) or []
    chunks: list[str] = []
    for item in output:
        content_list = getattr(item, "content", None) or []
        for content in content_list:
            if getattr(content, "type", "") != "output_text":
                continue
            text = str(getattr(content, "text", "") or "").strip()
            if text:
                chunks.append(text)
    return "".join(chunks).strip()


def _normalize_prediction(raw_text: str) -> str:
    """把 OCR 原始输出清洗成合法的 4 位验证码。"""
    allowed = set(ALPHABET)
    text = "".join(char for char in str(raw_text or "").upper() if char in allowed)
    return text[:CAPTCHA_LENGTH]


def solve_captcha_image_via_openai(
    image: Image.Image,
    *,
    model_name: str = DEFAULT_CAPTCHA_OCR_MODEL,
) -> str:
    """调用多模态模型识别验证码。"""
    client = build_openai_client()
    response = client.responses.create(
        model=model_name,
        max_output_tokens=16,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "你在做验证码 OCR。请只返回图片中的 4 个字符。"
                            "不要解释，不要空格，不要换行，不要 Markdown。"
                            f"字符只会来自这个集合：{ALPHABET}"
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": _image_to_data_url(image),
                    },
                ],
            }
        ],
    )
    prediction = _normalize_prediction(_extract_response_text(response))
    if len(prediction) != CAPTCHA_LENGTH:
        raise RuntimeError(f"{model_name} 返回了无效验证码结果：{prediction or '空结果'}")
    return prediction


def solve_captcha_image(image: Image.Image) -> str:
    """统一验证码识别入口；优先调用模型，失败再回退本地模板识别。"""
    try:
        return solve_captcha_image_via_openai(image)
    except Exception as exc:
        logger.warning("GPT 验证码识别失败，回退到本地模板识别：%s", exc)
        return solve_captcha_image_local(image)


def solve_captcha_bytes(data: bytes) -> str:
    """从图片二进制内容中识别验证码。"""
    return solve_captcha_image(Image.open(io.BytesIO(data)))


def solve_captcha_file(image_path: str | Path) -> str:
    """从本地图片文件中识别验证码。"""
    return solve_captcha_image(Image.open(image_path))


def run_self_check(rounds: int = 20, seed: int = 7) -> int:
    """跑一组本地自测，看看模板识别能命中多少轮。"""
    rng = random.Random(seed)
    success = 0

    for _ in range(rounds):
        answer = generate_captcha_text(rng=rng)
        image = render_captcha_image(answer, rng=rng)
        guess = solve_captcha_image_local(image)
        if guess == answer:
            success += 1

    return success


def main() -> int:
    """命令行入口：可做自测，也可导出一张样例验证码。"""
    parser = argparse.ArgumentParser(description="本地验证码生成与识别小工具。")
    parser.add_argument("--self-check", type=int, default=20, help="随机测试轮数，默认 20")
    parser.add_argument("--save-sample", default="", help="保存一张样例验证码到指定路径")
    args = parser.parse_args()

    rng = random.Random(20260310)
    sample_text = generate_captcha_text(rng=rng)

    if args.save_sample:
        sample_path = Path(args.save_sample)
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        render_captcha_image(sample_text, rng=rng).save(sample_path)
        print(f"样例验证码已保存: {sample_path}")
        print(f"样例答案: {sample_text}")

    success = run_self_check(rounds=args.self_check)
    print(f"自测通过: {success}/{args.self_check}")
    return 0 if success == args.self_check else 1


if __name__ == "__main__":
    raise SystemExit(main())
