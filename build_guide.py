#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the static Toastmasters treasure guide from public source pages."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMAGES = ROOT / "images"
DOC_ID = "DQmV5d2p5UXZhc3Nk"
DOC_PAGE = f"https://docs.qq.com/doc/{DOC_ID}"
DOC_API = f"https://docs.qq.com/dop-api/opendoc?id={DOC_ID}&normal=1&outformat=1&noEscape=1&commandsFormat=1&doc_chunk_version=3"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
FALLBACK_WECHAT = [
    "https://mp.weixin.qq.com/s/ih0JGBNC8KhO1aPcyPK9lA",
    "https://mp.weixin.qq.com/s/IUDP_lKZwpaMdV4W41eGDw",
    "https://mp.weixin.qq.com/s/lZ5Iqlcbs1c0sg3pYypqTQ",
    "https://mp.weixin.qq.com/s/ofweBuxq_3oksMPVL_EtuQ",
    "https://mp.weixin.qq.com/s/3V5bCNtOtKwcfSCu56E2Jg",
    "https://mp.weixin.qq.com/s/aMV4Hjeb8r5n9eItXwxP3w",
    "https://mp.weixin.qq.com/s/1ecqrFPs8SWQd_sETqPreg",
    "https://mp.weixin.qq.com/s/2mX3uM4Zo9Ul3_nyWRtziA",
]
NOISE = re.compile(r"^(?:picture|descript|p\.\d+@|宋体|黑体|微软雅黑|HYPERLINK|dlt inline|toc\s*\d+|Gothic|Calibri|SimSun)$", re.I)
CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]+")
DOC_IMAGE = re.compile(r"https?://docimg\d*\.docs\.qq\.com/[A-Za-z0-9._~:/?#@!$&'()*+,;=%\-]+")
WX_LINK = re.compile(r"https?://mp\.weixin\.qq\.com/s/[A-Za-z0-9_\-]+")


def fetch(url: str, referer: str, attempts: int = 3) -> bytes:
    error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer, "Accept": "*/*"})
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except Exception as exc:  # network retry boundary
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"抓取失败：{url} ({error})")


def unique(values):
    seen = set()
    return [value for value in values if not (value in seen or seen.add(value))]


def clean_line(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").strip(" \t\r\n|Z@")
    value = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9【（(]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) < 2 or NOISE.fullmatch(value):
        return ""
    if re.match(r"^p\.\d+", value, re.I) or "HYPERLINK" in value or "dlt inline" in value.lower():
        return ""
    if any(token.lower() in value.lower() for token in ("quote-cjk-patch", "segoe ui", "font-family", "calibri", "simsun", "微软雅黑", "宋体", "黑体")):
        return ""
    if not re.search(r"[\u4e00-\u9fff]", value) and len(value) < 8 and " " not in value:
        return ""
    if any(token.lower() in value.lower() for token in ("schemas.openxmlformats.org", "officeDocument/2006", "docProps/")):
        return ""
    return value


def source_data():
    payload = json.loads(fetch(DOC_API, "https://docs.qq.com/").decode("utf-8"))
    encoded = payload["clientVars"]["collab_client_vars"]["initialAttributedText"]["text"][0]
    decoded = base64.b64decode(encoded).decode("utf-8", "ignore")
    wx = unique(WX_LINK.findall(decoded))
    if len(wx) != 8:
        wx = FALLBACK_WECHAT
    return decoded, wx


def chapter_ranges(decoded: str):
    headings = ["会员篇", "俱乐部干事篇", "大区干事篇", "官网智能问答"]
    starts = []
    cursor = 0
    for heading in headings:
        matches = [m.start() for m in re.finditer(re.escape(heading), decoded)]
        candidates = [pos for pos in matches if pos >= cursor]
        # The first occurrence belongs to the contents; use the second where available.
        pos = candidates[1] if len(candidates) > 1 and cursor == 0 else (candidates[0] if candidates else -1)
        if pos < 0:
            raise RuntimeError(f"主文档缺少章节：{heading}")
        starts.append(pos)
        cursor = pos + len(heading)
    return [(headings[i], starts[i], starts[i + 1] if i + 1 < len(starts) else len(decoded)) for i in range(4)]


def extract_chapters(decoded: str):
    result = []
    for title, start, end in chapter_ranges(decoded):
        segment = decoded[start + len(title):end]
        segment = DOC_IMAGE.sub("\n", segment)
        lines = []
        for piece in CONTROL.split(segment):
            if re.search(r"p\.\d{8,}", piece) or "quote-cjk-patch" in piece:
                break
            line = clean_line(piece)
            if line and (not lines or lines[-1] != line):
                lines.append(line)
        # protobuf repeats metadata blocks; keep the substantive sequence from the first numbered item.
        first = next((i for i, line in enumerate(lines) if re.match(r"(?:\d+[、）]|一、|1[、）])", line)), 0)
        lines = lines[first:]
        garbage = next((i for i, line in enumerate(lines) if re.match(r"^p\.\d+", line) or "quote-cjk-patch" in line), len(lines))
        result.append({"title": title, "lines": lines[:garbage], "images": []})
    all_images = unique(DOC_IMAGE.findall(decoded))
    weights = [max(1, len(chapter["lines"])) for chapter in result]
    total_weight = sum(weights)
    cursor = 0
    for index, chapter in enumerate(result):
        end = len(all_images) if index == len(result) - 1 else cursor + round(len(all_images) * weights[index] / total_weight)
        chapter["images"] = all_images[cursor:end]
        cursor = end
    return result


def extension_from(url: str, content_type: str = "") -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    fmt = (query.get("wx_fmt") or [""])[0].lower()
    if fmt in {"jpeg", "jpg", "png", "gif", "webp"}:
        return ".jpg" if fmt == "jpeg" else f".{fmt}"
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return ext
    return mimetypes.guess_extension(content_type.split(";")[0]) or ".jpg"


def download_image(url: str, referer: str) -> str:
    ext = extension_from(url)
    name = hashlib.md5(url.encode()).hexdigest()[:12] + ext
    target = IMAGES / name
    if target.exists() and target.stat().st_size > 0:
        return f"images/{name}"
    raw = fetch(html.unescape(url), referer)
    target.write_bytes(raw)
    return f"images/{name}"


def js_string(page: str, variable: str) -> str:
    patterns = [rf"var\s+{re.escape(variable)}\s*=\s*'((?:\\.|[^'])*)'", rf'var\s+{re.escape(variable)}\s*=\s*"((?:\\.|[^"])*)"', rf'var\s+{re.escape(variable)}\s*=\s*htmlDecode\("([^"]*)"\)']
    for pattern in patterns:
        match = re.search(pattern, page)
        if match:
            value = match.group(1)
            try:
                value = bytes(value, "utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
            except Exception:
                pass
            return html.unescape(value)
    return ""


def meta_value(page: str, key: str) -> str:
    for match in re.finditer(r"<meta\b[^>]*>", page, re.I):
        tag = match.group(0)
        if re.search(rf'(?:name|property)=["\']{re.escape(key)}["\']', tag, re.I):
            value = re.search(r'content=["\']([^"\']*)', tag, re.I)
            return html.unescape(value.group(1)).strip() if value else ""
    return ""


def wechat_articles(urls):
    articles, failures = [], []
    for index, url in enumerate(urls):
        if index:
            time.sleep(2.5)
        try:
            page = fetch(url, "https://mp.weixin.qq.com/").decode("utf-8", "ignore")
            if "js_content" not in page:
                raise RuntimeError("页面未包含正文容器")
            title = js_string(page, "msg_title") or meta_value(page, "og:title") or f"延伸阅读 {index + 1}"
            nickname = js_string(page, "nickname") or "原文公众号"
            description = meta_value(page, "description") or meta_value(page, "og:description") or "点击进入微信公众号查看完整原文。"
            description = re.sub(r"\s+", " ", description)[:220]
            cover_url = meta_value(page, "og:image")
            cover = ""
            if cover_url and "mmbiz.qpic.cn" in cover_url:
                try:
                    cover = download_image(cover_url, "https://mp.weixin.qq.com/")
                except Exception:
                    cover = ""
            articles.append({"title": title.strip(), "nickname": nickname.strip(), "description": description, "url": url, "cover": cover})
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            articles.append({"title": f"延伸阅读 {index + 1}", "nickname": "抓取失败", "description": "暂时无法读取文章信息，请直接访问原文。", "url": url, "cover": "", "failed": True})
    return articles, failures


def linkify(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"(https?://[^\s，。；）】]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', escaped)


def render_line(line: str) -> str:
    content = linkify(line)
    if re.match(r"^(?:PS[:：]|注意事项|温馨提示)", line, re.I):
        return f'<aside class="tip"><b>温馨提示</b><p>{content}</p></aside>'
    if re.match(r"^\d+[、.]", line):
        return f'<h3>{content}</h3>'
    if re.match(r"^\d+[）)]", line):
        step_number = re.match(r"^\d+", line).group()
        return f'<div class="step"><span>{html.escape(step_number)}</span><p>{content}</p></div>'
    if line.endswith(("：", ":")) or re.match(r"^[A-Z][:：]", line):
        return f'<p class="emphasis">{content}</p>'
    return f'<p>{content}</p>'


def build_html(chapters, articles):
    toc_main = "".join(f'<a href="#chapter-{i+1}"><span>0{i+1}</span>{html.escape(c["title"])}</a>' for i, c in enumerate(chapters))
    toc_more = "".join(f'<a href="#reading-{i+1}"><span>{i+1:02d}</span>{html.escape(a["title"])}</a>' for i, a in enumerate(articles))
    sections = []
    for index, chapter in enumerate(chapters, 1):
        body = "".join(render_line(line) for line in chapter["lines"])
        gallery = "".join(f'<figure><img src="{src}" alt="{html.escape(chapter["title"])}操作截图" loading="lazy"></figure>' for src in chapter["local_images"])
        sections.append(f'<section class="chapter" id="chapter-{index}"><header><span>CHAPTER {index:02d}</span><h2>{html.escape(chapter["title"])}</h2></header><div class="prose">{body}</div><div class="gallery">{gallery}</div></section>')
    readings = []
    for index, article in enumerate(articles, 1):
        cover = f'<img src="{article["cover"]}" alt="{html.escape(article["title"])}" loading="lazy">' if article.get("cover") else '<div class="reading-mark">TM</div>'
        status = '<span class="failed">信息抓取失败</span>' if article.get("failed") else '<span>EXTENDED READING</span>'
        readings.append(f'<section class="reading" id="reading-{index}">{cover}<div>{status}<h2>{html.escape(article["title"])}</h2><p>{html.escape(article["description"])}</p><small>来源：公众号「{html.escape(article["nickname"]) }」</small><a href="{html.escape(article["url"])}" target="_blank" rel="noopener noreferrer">阅读微信公众号原文 ↗</a></div></section>')
    return TEMPLATE.replace("{{TOC_MAIN}}", toc_main).replace("{{TOC_MORE}}", toc_more).replace("{{CHAPTERS}}", "".join(sections)).replace("{{READINGS}}", "".join(readings)).replace("{{DOC_PAGE}}", DOC_PAGE)


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Toastmasters 官网操作指引：会员、俱乐部干事、大区干事与官网智能问答。"><title>Toastmasters 官网操作指引（宝藏资源篇）</title><style>
:root{--maroon:#772432;--navy:#004165;--gold:#c7983e;--gray:#53565a;--paper:#f5f6f5;--line:#dce2e1}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:#26343a;font:16px/1.78 Inter,"PingFang SC","Microsoft YaHei",sans-serif}.sidebar{position:fixed;inset:0 auto 0 0;width:310px;overflow:auto;padding:32px 24px;background:linear-gradient(165deg,#00344e,#004165 65%,#092f42);color:white}.brand{display:block;padding-bottom:24px;border-bottom:1px solid #ffffff25;color:white;text-decoration:none}.brand b{display:block;font:700 30px/1.2 Georgia,"Songti SC",serif}.brand small{color:#b8ced5;letter-spacing:.12em}.search{width:100%;margin:22px 0;padding:12px 14px;border:1px solid #ffffff40;border-radius:10px;background:#ffffff12;color:white;font-size:16px}.search::placeholder{color:#bbced4}.nav-group{margin:20px 0}.nav-group>strong{display:block;margin-bottom:9px;color:#e8c369;font-size:12px;letter-spacing:.14em}.nav-group a{display:flex;gap:10px;padding:9px 7px;border-radius:8px;color:#d8e6e9;text-decoration:none;font-size:14px}.nav-group a:hover{background:#ffffff12;color:white}.nav-group a span{color:#d9b45f;font:700 12px Georgia,serif}.content{max-width:1080px;margin-left:310px;padding:42px 58px 80px}.hero{padding:42px;border-radius:26px;background:linear-gradient(130deg,#fff,#f8f2e8);border:1px solid var(--line)}.hero span,.chapter header span,.reading>div>span{color:var(--maroon);font-size:12px;font-weight:900;letter-spacing:.16em}.hero h1{max-width:800px;margin:12px 0;font:700 clamp(38px,6vw,68px)/1.1 Georgia,"Songti SC",serif;color:var(--navy)}.hero p{max-width:760px;color:var(--gray)}.chapter{margin-top:34px;padding:34px;border-radius:22px;background:white;border:1px solid var(--line)}.chapter header{padding-bottom:20px;border-bottom:3px solid var(--maroon)}.chapter h2{margin:5px 0;color:var(--maroon);font:700 34px Georgia,"Songti SC",serif}.prose{max-width:820px}.prose h3{margin:32px 0 10px;color:var(--navy);font-size:22px}.prose p{margin:10px 0}.prose a{color:var(--navy);overflow-wrap:anywhere}.emphasis{padding:11px 14px;border-left:4px solid var(--navy);background:#edf4f5;font-weight:700}.step{display:grid;grid-template-columns:34px 1fr;gap:10px;align-items:start;margin:10px 0}.step span{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:var(--navy);color:white;font-weight:900}.step p{margin:0}.tip{margin:18px 0;padding:16px 18px;border-left:5px solid var(--gold);border-radius:10px;background:#fbf5e5}.tip b{color:#7b591d}.tip p{margin:4px 0}.gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:25px}.gallery figure{margin:0}.gallery img{display:block;width:100%;max-width:100%;border-radius:14px;border:1px solid var(--line);background:#eee}.readings-title{margin:58px 0 14px;color:var(--navy);font:700 36px Georgia,"Songti SC",serif}.copyright-note{padding:17px;border:1px solid #e2cf9a;border-radius:12px;background:#fff9e9;color:#65542e}.reading{display:grid;grid-template-columns:220px 1fr;gap:24px;margin:16px 0;padding:22px;border-radius:18px;background:white;border:1px solid var(--line)}.reading>img,.reading-mark{width:220px;height:150px;object-fit:cover;border-radius:12px;background:linear-gradient(135deg,var(--navy),#0d6c84);color:#e4bf63;display:grid;place-items:center;font:700 42px Georgia,serif}.reading h2{margin:7px 0;color:var(--navy);font:700 25px Georgia,"Songti SC",serif}.reading p{color:var(--gray)}.reading small{display:block}.reading a{display:inline-block;margin-top:12px;color:var(--maroon);font-weight:900}.failed{color:#9a263b!important}.footer{margin-top:50px;padding:28px;border-top:1px solid var(--line);color:#69777b}.footer a{color:var(--navy)}.nav-empty{display:none;padding:10px;color:#b8ced5}@media(max-width:820px){.sidebar{position:relative;width:auto;max-height:none;padding:22px}.brand b{font-size:24px}.nav-scroll{display:grid;grid-template-columns:1fr 1fr;gap:12px}.nav-group{margin:8px 0}.content{margin:0;padding:18px 14px 70px}.hero,.chapter{padding:22px 18px}.hero h1{font-size:38px}.chapter h2{font-size:28px}.gallery{grid-template-columns:1fr}.reading{grid-template-columns:1fr}.reading>img,.reading-mark{width:100%;height:180px}}@media(max-width:520px){.nav-scroll{grid-template-columns:1fr}.nav-group a{padding:7px}.hero h1{font-size:32px}.reading h2{font-size:21px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style></head><body><aside class="sidebar"><a class="brand" href="#top"><small>DISTRICT 118 · RESOURCE GUIDE</small><b>官网操作指引</b></a><input class="search" id="toc-search" type="search" placeholder="搜索目录…" aria-label="搜索目录"><div class="nav-scroll"><nav class="nav-group"><strong>主文档</strong>{{TOC_MAIN}}</nav><nav class="nav-group"><strong>延伸阅读</strong>{{TOC_MORE}}</nav></div><div class="nav-empty" id="nav-empty">没有匹配的目录项</div></aside><main class="content" id="top"><section class="hero"><span>TOASTMASTERS TREASURE RESOURCES</span><h1>Toastmasters 官网操作指引</h1><p>从会员日常操作到俱乐部与大区管理，把散落在官网中的实用入口整理成一份可查、可学、可回看的中文指南。</p></section>{{CHAPTERS}}<h2 class="readings-title">延伸阅读</h2><p class="copyright-note">以下内容仅提供来源、摘要和原文入口。文章及图片版权归原作者与原公众号所有，请点击链接阅读完整原文。</p>{{READINGS}}<footer class="footer">内容整理：王欢欢 · <a href="{{DOC_PAGE}}" target="_blank" rel="noopener noreferrer">主文档来源</a><br>延伸文章版权归原作者及原公众号所有。本页面仅用于Toastmasters公益学习交流。</footer></main><script>const q=document.querySelector('#toc-search'),links=[...document.querySelectorAll('.nav-group a')],empty=document.querySelector('#nav-empty');q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();let n=0;links.forEach(a=>{const show=!v||a.textContent.toLowerCase().includes(v);a.hidden=!show;if(show)n++});empty.style.display=n?'none':'block'});</script></body></html>'''


def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    decoded, wx_urls = source_data()
    chapters = extract_chapters(decoded)
    image_failures = []
    for chapter in chapters:
        chapter["local_images"] = []
        for url in chapter["images"]:
            try:
                chapter["local_images"].append(download_image(url, "https://docs.qq.com/"))
            except Exception as exc:
                image_failures.append({"url": url, "error": str(exc)})
    articles, article_failures = wechat_articles(wx_urls)
    (ROOT / "index.html").write_text(build_html(chapters, articles), encoding="utf-8")
    report = {
        "chapters": [{"title": c["title"], "lines": len(c["lines"]), "images": len(c["local_images"])} for c in chapters],
        "wechat_articles": len(articles), "article_failures": article_failures,
        "image_files": len(list(IMAGES.iterdir())), "image_failures": image_failures,
    }
    (ROOT / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
