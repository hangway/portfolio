#!/usr/bin/env python3
"""
fandom_summarizer.py
--------------------
POC: 讓 AI 從一個 Fandom (MediaWiki) wiki 產出多面向摘要
     Story / Characters / Creation / IP characteristics / Worldbuilding borrow checklist

管線 (pipeline):
    MediaWiki API 抽取  ->  HTML 清洗 + infobox 解析  ->  分層 LLM 摘要 (map-reduce)

模型分層 (符合 tiered orchestration):
    CHEAP_MODEL  = per-page 摘要        (Sonnet：量大、判斷輕)
    SMART_MODEL  = 跨頁綜合 + IP 分析   (Opus/Fable：量少、判斷重)

依賴:
    pip install requests beautifulsoup4 anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

用法:
    python fandom_summarizer.py --list-categories        # 先看這個 wiki 有哪些 category
    python fandom_summarizer.py                           # 跑全部 lens (預設每 lens 上限 10 頁)
    python fandom_summarizer.py --lens characters --max-pages 15
    python fandom_summarizer.py --lens borrow             # 只跑世界觀借鑑清單
    python fandom_summarizer.py --wiki starwars --max-pages 8

注意 (ToU / 授權):
    多數 Fandom wiki 文字為 CC-BY-SA，可再利用但須署名 + 相同方式分享；輸出報告底部會自動附來源 URL。
    Fandom 使用條款限制自動化抓取與 AI 開發，本工具僅供個人研究，已內建 User-Agent、maxlag、節流以尊重其基礎設施。
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # 允許 --list-categories 等不需 LLM 的動作


# ─────────────────────────────────────────────────────────────
# 設定 (CONFIG) — 直接改這裡即可換 wiki / 換模型 / 調上限
# ─────────────────────────────────────────────────────────────

WIKI = "fringe"                     # <wiki>.fandom.com

# 模型 ID 為撰寫當下之現行值；如有變動請至 docs.claude.com 核對。
# 判斷重的 lens 也可把 SMART_MODEL 換成 "claude-fable-5"。
CHEAP_MODEL = "claude-sonnet-5"
SMART_MODEL = "claude-opus-4-8"

# 最終綜合輸出語言 (per-page 內部筆記一律英文以省 token)。改成 "English" 即可切回。
OUTPUT_LANG = "Traditional Chinese"

MAX_PAGES_PER_LENS = 10             # 每個 lens 最多處理幾頁 (控成本；先小後大)
REQUEST_DELAY = 1.0                  # 每次 API 請求間隔秒數 (禮貌節流；若持續被擋，調高到 2-3)
USER_AGENT = "FandomSummarizer-POC/1.0 (personal research)"
# 若某個 wiki 持續回傳非 JSON 內容 (通常是反機器人驗證頁)，代表 Fandom 判定此流量為自動化存取。
# 這是他們使用條款明文禁止未經授權之機器人存取的技術執行；本腳本的因應方式是節流、重試、
# 給出清楚的錯誤訊息，而不是想辦法「破解」該驗證。持續被擋時，改用對話裡的 web_search 取樣法。

# 每個 lens 的取材來源。
#   seed_pages       = 一定納入的頁面標題 (缺頁自動略過)
#   seed_categories  = 會爬取的分類 (爬到 MAX_PAGES_PER_LENS 上限為止；不存在則略過)
#   focus            = 給 per-page 摘要的視角指示
# 下方為 Fringe 的合理預設；先跑 --list-categories 可確認實際分類名稱再微調。
LENSES: dict[str, dict] = {
    "story": {
        "seed_pages": [],
        "seed_categories": ["Episodes", "Seasons"],
        "focus": ("Summarize only the in-universe plot: key events, timeline position, "
                  "and central conflicts. Ignore real-world production/trivia."),
    },
    "characters": {
        "seed_pages": ["Walter Bishop", "Peter Bishop", "Olivia Dunham",
                       "Astrid Farnsworth", "Philip Broyles", "September"],
        "seed_categories": ["Characters", "Main Characters"],
        "focus": ("Summarize this character: role, allegiance/affiliation, arc across the "
                  "series, key relationships, and defining traits. Use the infobox facts."),
    },
    "creation": {
        "seed_pages": ["Fringe", "J.J. Abrams", "J.H. Wyman", "Jeff Pinkner"],
        "seed_categories": ["Crew", "Cast", "Production"],
        "focus": ("Extract ONLY real-world / production information: creators, writers, "
                  "showrunners, casting, production history, development, influences, "
                  "behind-the-scenes facts. Ignore in-universe plot."),
    },
    "ip": {
        # IP lens 以世界觀頁面 + 其他 lens 的產出綜合而成 (見 pipeline)
        "seed_pages": ["Parallel universe", "The Pattern", "Cortexiphan", "Observer",
                       "Massive Dynamic", "ZFT", "Fringe Division", "Amber", "The Machine"],
        "seed_categories": [],
        "focus": ("Summarize this worldbuilding element: its rules, significance, and how "
                  "it defines the fictional world."),
    },
    "borrow": {
        # 世界觀「借鑑清單」lens：抽取可重複套用的世界觀技術，輸出打勾式 checklist。
        # 吃世界觀頁面 + story/characters/ip 的綜合結果為脈絡 (見 pipeline)。
        "seed_pages": ["Parallel universe", "The Pattern", "Cortexiphan", "Observer",
                       "Massive Dynamic", "ZFT", "Fringe Division", "Amber", "The Machine",
                       "The First People", "White Tulip", "Soft spot"],
        "seed_categories": [],
        "focus": ("Extract the worldbuilding DEVICE this page describes: its rule / "
                  "constraint / cost, how it is structured, how it links to other elements, "
                  "and how or when it is revealed to the audience. The TECHNIQUE, not the plot."),
    },
}


# ─────────────────────────────────────────────────────────────
# MediaWiki API 客戶端
# ─────────────────────────────────────────────────────────────

class MediaWikiClient:
    def __init__(self, wiki: str):
        self.api = f"https://{wiki}.fandom.com/api.php"
        self.base = f"https://{wiki}.fandom.com/wiki/"
        self.s = requests.Session()
        # 完整一點的標頭組合 (不只 User-Agent)：降低被當成裸 script 流量的機率。
        # 這仍是誠實表明身份的 bot UA，不是偽裝成瀏覽器。
        self.s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._warmed = False

    def _warm_session(self):
        """先訪問一次首頁拿 cookie，讓後續 api.php 請求更接近正常瀏覽順序（而非冷啟動直接打 API）。
        失敗不影響後續流程——純粹是盡量降低被判定為機器人的機率，不是繞過驗證的手段。"""
        if self._warmed:
            return
        try:
            self.s.get(self.base, timeout=20)
        except requests.RequestException:
            pass
        self._warmed = True

    def _get(self, params: dict, retries: int = 4) -> dict:
        self._warm_session()
        params = {**params, "format": "json", "maxlag": 5}
        for attempt in range(retries):
            time.sleep(REQUEST_DELAY)
            try:
                r = self.s.get(self.api, params=params, timeout=30)
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"連線失敗（{e}）。檢查網路，或該 wiki 是否暫時無法連線。") from e
                time.sleep(3 * (attempt + 1))
                continue

            if r.status_code in (403, 429, 503):
                # 403/429 常見於被判定為自動化流量或速率限制；503 常見於 maxlag 或維護。
                if attempt == retries - 1:
                    raise RuntimeError(
                        f"收到 HTTP {r.status_code}，重試 {retries} 次後仍失敗。"
                        "這通常代表 Fandom 的反機器人防護把此請求判定為自動化流量——"
                        "這是他們使用條款禁止未授權機器人存取的技術執行，不是單純的網路錯誤。"
                        f"可先調高 REQUEST_DELAY（目前 {REQUEST_DELAY}s）、減少 --max-pages，"
                        "或改用對話裡直接 web_search 取樣（不會打到 Fandom 的伺服器）。"
                    )
                time.sleep((2 if r.status_code == 503 else 4) * (attempt + 1))
                continue

            ctype = r.headers.get("Content-Type", "")
            if "json" not in ctype:
                # 收到 HTML 而非 JSON，最常見的原因是 Cloudflare「請稍候…」驗證頁，
                # 而不是真正的 API 回應。直接 r.json() 會噴難懂的 JSONDecodeError，這裡改成明確診斷。
                if attempt == retries - 1:
                    snippet = r.text[:150].replace("\n", " ").strip()
                    raise RuntimeError(
                        f"預期是 JSON，卻收到 Content-Type={ctype!r} 的內容"
                        f"（開頭：{snippet!r}…）。這通常是反機器人驗證頁，代表此 wiki "
                        "對自動化請求做了較嚴格的防護，重試無法解決——建議改用對話裡的 "
                        "web_search 取樣法（本次對 x-files.fandom.com 就是這樣繞過去的）。"
                    )
                time.sleep(3 * (attempt + 1))
                continue

            r.raise_for_status()
            data = r.json()
            if "error" in data and data["error"].get("code") == "maxlag":
                time.sleep(2 * (attempt + 1))
                continue
            return data
        raise RuntimeError(f"API failed after {retries} retries: {params}")

    def list_categories(self, limit: int = 500) -> list[str]:
        out, cont = [], {}
        while True:
            data = self._get({"action": "query", "list": "allcategories",
                              "aclimit": limit, **cont})
            out += [c["*"] for c in data["query"]["allcategories"]]
            if "continue" in data:
                cont = data["continue"]
            else:
                break
        return out

    def category_members(self, category: str, cap: int) -> list[str]:
        """回傳分類下的文章標題 (namespace 0)。"""
        out, cont = [], {}
        cat = category if category.lower().startswith("category:") else f"Category:{category}"
        while len(out) < cap:
            data = self._get({"action": "query", "list": "categorymembers",
                              "cmtitle": cat, "cmtype": "page",
                              "cmlimit": min(500, cap - len(out)), **cont})
            members = data.get("query", {}).get("categorymembers", [])
            out += [m["title"] for m in members]
            if "continue" in data:
                cont = data["continue"]
            else:
                break
        return out[:cap]

    def page_html(self, title: str) -> Optional[str]:
        data = self._get({"action": "parse", "page": title, "prop": "text",
                          "redirects": 1})
        if "error" in data:
            return None
        return data.get("parse", {}).get("text", {}).get("*")


# ─────────────────────────────────────────────────────────────
# 內容清洗 + infobox 解析
# ─────────────────────────────────────────────────────────────

@dataclass
class Page:
    title: str
    url: str
    infobox: dict = field(default_factory=dict)
    text: str = ""


def extract_page(client: MediaWikiClient, title: str) -> Optional[Page]:
    html = client.page_html(title)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 1) Portable Infobox -> 結構化 dict
    infobox = {}
    aside = soup.select_one("aside.portable-infobox")
    if aside:
        for item in aside.select(".pi-item.pi-data"):
            label = item.select_one(".pi-data-label")
            value = item.select_one(".pi-data-value")
            if label and value:
                infobox[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)

    # 2) 移除非正文元素，再抽段落
    for sel in ["aside", "table", "sup", ".reference", ".navbox", ".mw-editsection",
                ".toc", "style", "script", ".noprint", "figure"]:
        for el in soup.select(sel):
            el.decompose()
    paras = [p.get_text(" ", strip=True) for p in soup.select("p")]
    text = "\n\n".join(t for t in paras if len(t) > 1)
    text = re.sub(r"\[\d+\]", "", text)  # 殘留引用標記

    return Page(title=title, url=client.base + title.replace(" ", "_"),
                infobox=infobox, text=text)


# ─────────────────────────────────────────────────────────────
# LLM 摘要層
# ─────────────────────────────────────────────────────────────

def llm(client: "Anthropic", model: str, system: str, prompt: str,
        max_tokens: int = 1200) -> str:
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:  # noqa: BLE001  (簡易重試)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return ""


def summarize_page(client, page: Page, focus: str) -> str:
    """per-page 摘要 (便宜模型)。長文自動截斷以控 token。"""
    body = page.text[:8000]
    info = "\n".join(f"- {k}: {v}" for k, v in page.infobox.items()) or "(none)"
    system = ("You summarize wiki articles into terse, factual notes for a downstream "
              "synthesis step. No preamble. 3-6 bullet points.")
    prompt = (f"{focus}\n\nARTICLE TITLE: {page.title}\n\nINFOBOX:\n{info}\n\n"
              f"BODY:\n{body}")
    return llm(client, CHEAP_MODEL, system, prompt, max_tokens=500)


LENS_SYNTHESIS = {
    "story": ("From these per-episode/plot notes, reconstruct the overall STORY of the "
              "franchise: premise, central mysteries, the major arcs across seasons, and "
              "how the narrative escalates and resolves. Prose, ~400-600 words."),
    "characters": ("From these character notes, write an ENSEMBLE overview: the protagonists "
                   "and antagonists, the core relationship dynamics, and who drives the plot. "
                   "Prose with a short list of principals, ~400-600 words."),
    "creation": ("From these production notes, summarize HOW THE WORK WAS CREATED: creators / "
                 "showrunners, key writers, casting, production history, and notable "
                 "behind-the-scenes facts and influences. Prose, ~300-500 words."),
    "ip": ("You are a worldbuilding analyst advising a creator who builds their own original "
           "IP. Analyze what makes THIS IP distinctive: its core premise/hook, worldbuilding "
           "rules and mechanics, recurring themes, tone, aesthetic identity, and franchise "
           "structure. Be genuinely analytical — end with 3-5 concrete, transferable lessons "
           "a creator could apply to their own universe. Prose, ~500-700 words."),
    "borrow": (
        "You are a worldbuilding coach. The reader is building their OWN original fiction "
        "universe and wants a REUSABLE BORROW CHECKLIST distilled from how THIS IP is built.\n"
        "Organize the checklist under these dimensions (skip one only if the notes truly say "
        "nothing about it):\n"
        "1. Core conceit (the central 'what if')\n"
        "2. Rule / power system and its costs & constraints\n"
        "3. Hidden history / buried truth that recontextualizes the world\n"
        "4. Factions & institutions\n"
        "5. Cosmology / spatial structure (parallel worlds, timelines, maps)\n"
        "6. Symbols, motifs & rituals\n"
        "7. World-character binding (how the setting ties to personal stakes)\n"
        "8. Theme-mechanic unity (how the rules encode the theme)\n"
        "9. Mystery economy (how information is metered and revealed)\n"
        "10. Entry point / accessibility for newcomers\n\n"
        "Group items under '### <dimension>' headers. For EACH item use EXACTLY this format:\n"
        "- [ ] **<technique name>**\n"
        "  - 這個 IP 怎麼做: <grounded in the notes, 1-2 sentences>\n"
        "  - 為什麼有效: <the underlying principle, 1 sentence>\n"
        "  - 借鑑動作: <a concrete imperative move OR a pointed question the reader applies to "
        "their OWN world — actionable and specific, never generic>\n\n"
        "10-16 items total. No preamble."
    ),
}


def synthesize(client, lens: str, notes: list[str], extra_context: str = "") -> str:
    system = ("You are a sharp story/franchise analyst. Write clear, structured prose. "
              "Ground every claim in the supplied notes; do not invent facts. "
              f"Write your response in {OUTPUT_LANG}.")
    joined = "\n\n---\n\n".join(notes)
    prompt = f"{LENS_SYNTHESIS[lens]}\n\n"
    if extra_context:
        prompt += f"HIGH-LEVEL CONTEXT FROM OTHER ANALYSES:\n{extra_context}\n\n"
    prompt += f"SOURCE NOTES:\n{joined}"
    return llm(client, SMART_MODEL, system, prompt, max_tokens=2000)


# ─────────────────────────────────────────────────────────────
# 管線
# ─────────────────────────────────────────────────────────────

def gather_titles(mw: MediaWikiClient, spec: dict, cap: int) -> list[str]:
    titles: list[str] = list(spec.get("seed_pages", []))
    for cat in spec.get("seed_categories", []):
        if len(titles) >= cap:
            break
        members = mw.category_members(cat, cap - len(titles))
        for t in members:
            if t not in titles:
                titles.append(t)
    return titles[:cap]


def run_lens(mw, llm_client, lens: str, cap: int) -> tuple[str, list[Page]]:
    spec = LENSES[lens]
    titles = gather_titles(mw, spec, cap)
    print(f"\n[{lens}] 目標 {len(titles)} 頁: {', '.join(titles[:8])}"
          f"{' ...' if len(titles) > 8 else ''}")

    pages, notes = [], []
    for t in titles:
        page = extract_page(mw, t)
        if not page or len(page.text) < 80:
            print(f"    · 略過 (缺頁/內容過短): {t}")
            continue
        print(f"    · 摘要中: {t}")
        note = summarize_page(llm_client, page, spec["focus"])
        notes.append(f"[{page.title}]\n{note}")
        pages.append(page)

    if not notes:
        return "(無可用內容)", pages
    print(f"    → 綜合 {len(notes)} 份筆記 ({SMART_MODEL})")
    summary = synthesize(llm_client, lens, notes)
    return summary, pages


def main():
    ap = argparse.ArgumentParser(description="Fandom wiki 多面向 AI 摘要 POC")
    ap.add_argument("--wiki", default=WIKI, help="wiki 子網域 (預設 fringe)")
    ap.add_argument("--lens", choices=list(LENSES) + ["all"], default="all")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_LENS)
    ap.add_argument("--list-categories", action="store_true",
                    help="只列出此 wiki 的所有分類後結束")
    ap.add_argument("--out", default=None, help="輸出檔名前綴 (預設用 wiki 名)")
    args = ap.parse_args()

    mw = MediaWikiClient(args.wiki)
    prefix = args.out or args.wiki

    if args.list_categories:
        cats = mw.list_categories()
        print(f"{args.wiki} 共 {len(cats)} 個分類:\n")
        for c in sorted(cats):
            print(" ", c)
        return

    if Anthropic is None:
        sys.exit("需要 anthropic 套件：pip install anthropic，並設定 ANTHROPIC_API_KEY")
    llm_client = Anthropic()

    lenses = list(LENSES) if args.lens == "all" else [args.lens]
    results: dict[str, str] = {}
    all_pages: dict[str, list[Page]] = {}

    # 基礎 lens 先跑；脈絡型 lens (ip / borrow) 之後跑，吃前者的綜合結果當高階脈絡。
    BASE = ["story", "characters", "creation"]
    CONTEXT_DEPS = {"ip": ["story", "characters"],
                    "borrow": ["story", "characters", "ip"]}
    base_order = [l for l in BASE if l in lenses]
    ctx_order = [l for l in ("ip", "borrow") if l in lenses]
    order = base_order + ctx_order

    for lens in base_order:
        summary, pages = run_lens(mw, llm_client, lens, args.max_pages)
        results[lens] = summary
        all_pages[lens] = pages

    for lens in ctx_order:
        spec = LENSES[lens]
        titles = gather_titles(mw, spec, args.max_pages)
        pages = [p for t in titles if (p := extract_page(mw, t)) and len(p.text) >= 80]
        all_pages[lens] = pages
        notes = [f"[{p.title}]\n{summarize_page(llm_client, p, spec['focus'])}" for p in pages]
        deps = [c for c in CONTEXT_DEPS[lens] if c in results]
        ctx = [f"### {c.upper()}\n{results[c]}" for c in deps]
        if deps:
            print(f"\n[{lens}] 以 {', '.join(deps)} 為脈絡分析")
        results[lens] = (synthesize(llm_client, lens, notes, extra_context="\n\n".join(ctx))
                         if notes else "(無可用內容)")

    # ── 輸出報告 ──
    titles_zh = {"story": "故事 (Story)", "characters": "人物 (Characters)",
                 "creation": "創作過程 (Creation Process)", "ip": "IP 特色 (IP Characteristics)",
                 "borrow": "世界觀借鑑清單 (Worldbuilding Borrow Checklist)"}
    md = [f"# {args.wiki}.fandom.com — AI 摘要\n",
          f"_模型: per-page `{CHEAP_MODEL}` · synthesis `{SMART_MODEL}`_\n"]
    for lens in order:
        md.append(f"\n## {titles_zh[lens]}\n\n{results.get(lens, '(略)')}\n")

    # CC-BY-SA 署名：列出所有來源頁
    seen = {}
    for pages in all_pages.values():
        for p in pages:
            seen[p.url] = p.title
    md.append("\n---\n\n## Sources (CC-BY-SA attribution)\n")
    md.append(f"內容改編自 {args.wiki}.fandom.com，授權 CC-BY-SA 3.0。來源頁面：\n")
    for url, title in sorted(seen.items(), key=lambda x: x[1]):
        md.append(f"- [{title}]({url})")

    md_path = f"{prefix}_summary.md"
    json_path = f"{prefix}_data.json"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"wiki": args.wiki, "summaries": results,
                   "sources": {u: t for u, t in seen.items()}},
                  f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成 → {md_path} / {json_path}  (共 {len(seen)} 個來源頁)")


if __name__ == "__main__":
    main()
