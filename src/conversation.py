"""Bounded session-local conversation memory and conservative follow-up routing.

User utterances are context, never evidence. Only committed turns influence a
later request. This module does not call a model or read any credential/file.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
import time
import unicodedata

MAX_TURNS = 20
MAX_HISTORY_BYTES = 180_000
PENDING_TTL = 90
MAX_SEARCH_CHARS = 200
MAX_PENDING = 2

SUGGESTIONS = ["論文紙本要交幾本？", "口試要怎麼申請？", "論文可以單面列印嗎？"]
DOMAIN = re.compile(r"論文|论文|口試|口试|學位|学位|離校|离校|畢業|毕业|圖書館|图书馆|註冊組|注册组|延後公開|延后公开|單面|单面|雙面|双面|封面|送印|借的書|借的书|還書|还书|滯還金|滞还金")
TOPICS = [
    ("copies", re.compile(r"幾本|几本|份數|份数|一本|二本|兩本|两本|紙本|纸本|繳交.*論文|缴交.*论文"), "論文紙本繳交份數"),
    ("printing", re.compile(r"列印|打印|印刷|單面|单面|雙面|双面|[0-9]+\s*頁|[0-9]+\s*页"), "論文列印方式"),
    ("defer", re.compile(r"延後公開|延后公开"), "論文延後公開申請"),
    ("exam", re.compile(r"口試|口试|學位考試|学位考试"), "學位考試申請"),
    ("cover", re.compile(r"封面"), "論文封面格式"),
    ("library_returns", re.compile(r"借的書|借的书|還書|还书|滯還金|滞还金|借書.*處理|借书.*处理"), "離校圖書館借還手續"),
    ("version", re.compile(r"適用版本|适用版本|哪.*(?:年的|年).*版本|哪.*版本"), "論文規定適用版本"),
    ("leaving", re.compile(r"離校|离校|畢業|毕业"), "畢業離校程序"),
]
THANKS_PREFIX = re.compile(r"^(?:謝啦|谢啦|謝謝|谢谢|感謝|感谢|多謝|多谢|thank you|thanks)[,，。!！\s]*", re.I)
EXACT_GREETING = re.compile(r"^(?:你好|您好|嗨|哈囉|哈啰|hello|hi)[!！。,.，\s]*$", re.I)
ACKNOWLEDGEMENT = re.compile(r"^(?:謝謝|谢谢|感謝|感谢|thank you|thanks)[!！。,.，\s]*$", re.I)
CAPABILITIES = re.compile(r"^(?:你(?:能|可以)(?:幫我|帮我)?(?:做什麼|做什么|做哪些事|幫我什麼|帮我什么)|你能幫我什麼|你能帮我什么|有什麼功能|有什么功能|怎麼使用|怎么使用)[?？!！。\s]*$")
OUTSIDE = re.compile(r"下雨|下雪|氣溫|气温|天氣|天气|股價|股价|股票行情|講.*笑話|讲.*笑话|笑話|笑话|比特幣|比特币|樂透|乐透")
DEGREE_FOLLOWUP = re.compile(r"^(?:那|如果是|若是|我是|改成|更正為|更正为)?\s*(碩士|硕士|博士)(?:班)?(?:呢|的話|的话)?[?？!！。\s]*$")
RECIPIENT_FOLLOWUP = re.compile(r"^(?:那|如果是|至於|至于)?\s*(註冊組|注册组|教務處|教务处|圖書館|图书馆)(?:的|端)?(?:呢|要幾本|要几本|要交幾本|要交几本)?[?？!！。\s]*$")
PAGES_FOLLOWUP = re.compile(r"^(?:那|如果是|若是|改成)?\s*([0-9]{1,4})\s*[頁页](?:呢|的話|的话|可以單面嗎|可以单面吗)?[?？!！。\s]*$")
GENERIC_FOLLOWUP = re.compile(r"^(?:那)?(?:要幾本|要几本|要多久|什麼時候|什么时候|怎麼辦|怎么办|怎麼做|怎么做|這個|这个|那個|那个|它)(?:呢|嗎|吗)?[?？!！。\s]*$")


def norm(text):
    return unicodedata.normalize("NFKC", text).strip()


def topic_for(text):
    value = norm(text)
    # More specific semantic subjects precede generic paper/copy words.
    for key in ("defer", "library_returns", "version", "printing", "cover", "exam", "copies", "leaving"):
        for name, pattern, label in TOPICS:
            if name == key and pattern.search(value):
                return name, label
    return None, None


def new_memory():
    return {"generation": secrets.token_urlsafe(16), "turns": [], "conditions": {},
            "skipped": [], "pending": {}, "receipts": {}, "version": 0,
            "topic": None, "topic_question_id": None, "topic_search_text": None, "pending_conditions": []}


def _friendly(kind, message, title):
    return {"response_kind": kind, "message": message, "title": title,
            "suggestions": SUGGESTIONS[:], "search_text": None,
            "context_topic": None, "context_note": None}


def _degree(value):
    return "碩士" if value in ("碩士", "硕士") else "博士"


def degree_update(value):
    """Parse only explicit degree corrections, never infer the other degree.

    Returns (action, value). The original utterance still enters v0.5 unchanged.
    """
    pair = re.search(r"(?:我)?不是\s*(碩士|硕士|博士)(?:班)?\s*[,，;；]\s*(?:而)?(?:我是|是)\s*(碩士|硕士|博士)(?:班)?", value)
    if pair:
        if len(re.findall(r"碩士|硕士|博士", value)) != 2:
            return "ambiguous", None
        a, b = _degree(pair[1]), _degree(pair[2])
        if a != b:
            return "set", b
        return "ambiguous", None
    negated = [_degree(x) for x in re.findall(r"(?:不是|並非|并非)\s*(碩士|硕士|博士)(?:班)?", value)]
    positive = [_degree(x) for x in re.findall(r"(?:我是|更正為|更正为|改成)\s*(碩士|硕士|博士)(?:班)?", value)]
    mentions = {_degree(x) for x in re.findall(r"碩士|硕士|博士", value)}
    if positive and len(set(positive)) == 1 and not negated and len(mentions) == 1:
        return "set", positive[-1]
    if negated and not positive:
        return "revoke", list(dict.fromkeys(negated))
    if positive or negated:
        return "ambiguous", None
    return "none", None


def _context_base(memory, label, degree=None):
    base = memory.get("topic_search_text") or label or ""
    if degree:
        base = re.sub(r"(?:碩士|硕士|博士)(?:班)?", "", base)
        base = degree + " " + base
    return base


def plan(clean, memory):
    """Trusted, bounded intent plan; no answer facts or gold are added here."""
    text = clean["query_text"]
    value = norm(text)
    thanks = THANKS_PREFIX.match(value)
    closing = thanks and not re.search(r"[?？]|呢|嗎|吗|怎麼|怎么|如何|是否|能不能|可不可以|幾|几|什麼|什么|多少|誰|谁|哪", value[thanks.end():]) and (not value[thanks.end():].strip() or re.search(r"先去|先準備|先准备|我.*(?:去|準備|准备)|先這樣|先这样|就這樣|就这样|先忙|了解了|明白了|知道了|收到", value[thanks.end():]))
    if ACKNOWLEDGEMENT.fullmatch(value) or closing:
        out = _friendly("acknowledgement", "不客氣！還有論文、口試或離校問題，都可以接著問。", "不客氣，隨時接著問")
        out["suggestions"] = []
        return out
    # Strip a thanks prefix only for intent recognition; raw query_text is intact.
    if thanks:
        value = value[thanks.end():].strip()
    if EXACT_GREETING.fullmatch(value):
        return _friendly("greeting", "你好！我可以幫你整理論文、口試與離校相關資訊。你想先了解哪一項？", "你好，歡迎")
    if CAPABILITIES.fullmatch(value):
        return _friendly("capabilities", "我可以整理論文份數與格式、口試申請和離校文件，並附上來源原文；條件不清楚時會先補問。", "我能幫你整理這些事")
    if OUTSIDE.search(value) and not DOMAIN.search(value):
        return _friendly("clear_out_of_scope", "這一版專門協助論文、口試與離校查證，還沒有提供這類資訊。你也可以接著問下面這些問題。", "這題超出目前服務範圍")

    conditions = copy.deepcopy(memory["conditions"])
    skips = set(memory["skipped"])
    pending = set(memory.get("pending_conditions", []))
    for slot in clean["clarify_skipped"]:
        conditions.pop(slot, None); skips.add(slot)
    for slot, item in clean["conditions"].items():
        conditions[slot] = copy.deepcopy(item); skips.discard(slot); pending.discard(slot)

    last_topic = memory.get("topic")
    label = next((t[2] for t in TOPICS if t[0] == last_topic), None)
    subject, subject_label = topic_for(value)
    used = None
    effective = text
    intent = None
    degree = DEGREE_FOLLOWUP.fullmatch(value)
    recipient = RECIPIENT_FOLLOWUP.fullmatch(value)
    pages = PAGES_FOLLOWUP.fullmatch(value)
    generic = GENERIC_FOLLOWUP.fullmatch(value)
    update, degree_value = degree_update(value)
    # An explicitly filled field is already validated by the request contract.
    # A contradictory natural-language correction is not silently discarded.
    if update == "set":
        if "degree_level" in clean["conditions"] and clean["conditions"]["degree_level"] != degree_value:
            update = "ambiguous"
        else:
            conditions["degree_level"] = degree_value; skips.discard("degree_level"); pending.discard("degree_level")
    if update == "revoke":
        denied = degree_value
        if conditions.get("degree_level") in denied:
            conditions.pop("degree_level", None)
        if "degree_level" not in conditions:
            pending.add("degree_level"); skips.discard("degree_level")
        out = _friendly("profile_update", "你否定的學位別不會再作為這個聊天的條件；新的學位別尚待確認，請補充正確學位別。", "先前學位條件已撤下")
        out.update(conditions=conditions, clarify_skipped=sorted(skips), pending_conditions=sorted(pending))
        return out
    if update == "ambiguous":
        # Do not keep a possibly contradicted remembered degree as confirmed.
        conditions.pop("degree_level", None); skips.discard("degree_level"); pending.add("degree_level")
        out = _friendly("profile_update", "這句包含尚未釐清的學位條件，已暫停沿用先前學位別。請明確確認目前要查碩士或博士。", "請確認學位別")
        out.update(conditions=conditions, clarify_skipped=sorted(skips), pending_conditions=sorted(pending))
        return out

    # No regex assignment for years, including a degree correction accompanied
    # by an ambiguous calendar/academic year. Preserve only the explicit degree.
    if re.search(r"不是|並非|并非|不對|不对|更正|改成", value) and re.search(r"[0-9]{2,4}.*(?:年|學期|学期)", value) and not ({"enrollment_year", "target_academic_term"} & set(clean["conditions"])):
        out = _friendly("profile_update" if update == "set" else "needs_context", "年份更正需要分清入學學年度與目標學期；我不會把日曆年自動填成學年度。請使用條件欄位指定。", "先確認年份的意思")
        if update == "set": out.update(conditions=conditions, clarify_skipped=sorted(skips), pending_conditions=sorted(pending))
        return out

    # Standalone explicit correction is useful even without another school query.
    pair_only = re.fullmatch(r"(?:我)?不是\s*(?:碩士|硕士|博士)(?:班)?\s*[,，;；]\s*(?:而)?(?:我是|是)\s*(?:碩士|硕士|博士)(?:班)?[。!?！？\s]*", value)
    profile_only = re.fullmatch(r"(?:更正[,，:：\s]*)?(?:我是|更正為|更正为|改成)\s*(?:碩士|硕士|博士)(?:班)?[。!?！？\s]*", value)
    if update == "set" and (pair_only or profile_only):
        out = _friendly("profile_update", f"已將這個聊天的學位別更新為{degree_value}；這是你提供的條件，不代表文件已確認適用。你可以接著追問。", "學位條件已更新")
        out.update(conditions=conditions, clarify_skipped=sorted(skips), pending_conditions=sorted(pending))
        return out

    # Specific follow-up intents outrank a generic word such as 紙本.
    exam_context = last_topic == "exam" or subject == "exam"
    sender_permission = bool(exam_context and re.search(r"(?:學生|学生|本人|我|自己|自行|代送).*(?:送|交).*(?:可|能|行|嗎|吗)|(?:自己|自行|代送).*(?:送|交)", value))
    processing_duration = bool(exam_context and (re.search(r"(?:註冊組|注册组|審核|审核|審好|审好|辦好|办好).*(?:幾|几|多久|工作天|天內|天内)|(?:幾個|几个).*工作天", value)))
    sender = bool(re.search(r"(?:由誰|由谁|誰|谁).*(?:送|交)|誰送件|谁送件", value))
    paper_docs = bool(re.search(r"(?:繳|缴|交).*(?:紙本|纸本).*(?:帶|带|準備|准备|什麼|什么|文件|資料|资料)|(?:佐證|佐证|證明|证明).*(?:影本|複印|复印|正本)|(?:申請書|申请书|佐證|佐证).*(?:裝訂|装订)", value))
    version = bool(re.search(r"最新.*(?:版本|版)|(?:用|採用|采用|依).*?(?:版本|版).*?(?:就好|可以|適用|适用)|哪.*(?:年的|年).*版本|(?:採用|采用).*版|適用版本|适用版本|年.*(?:入學|入学).*(?:適用|适用)|(?:適用|适用).*(?:這份|这份|格式|條例|条例)|(?:這份|这份|格式|條例|条例).*(?:適用|适用)", value))
    confirmation = bool(re.search(r"(?:哪|何).*(?:單位|单位|網頁|网页|網站|网站).*(?:確認|确认|查)|(?:去哪|到哪|向誰|向谁).*(?:確認|确认)|(?:單位|单位|網頁|网页|網站|网站).*確認", value))
    library_returns = bool(re.search(r"借的書|借的书|還書|还书|滯還金|滞还金|借書.*處理|借书.*处理", value))
    duration = bool(generic and re.search(r"多久", value))
    if library_returns:
        effective = f"圖書館借書歸還 滯還金 離校；{text}"
        subject, subject_label = "library_returns", "離校圖書館借還手續"
        intent = "library_returns"
        if last_topic in ("leaving", "library_returns"): used = memory.get("topic_question_id")
    elif sender_permission:
        effective = f"學位考試申請手續 註冊組 送件 自行送件；{text}"
        subject, subject_label = "exam", label or "學位考試申請"
        used = memory.get("topic_question_id"); intent = "sender_permission"
    elif processing_duration:
        effective = f"學位考試申請 註冊組 審核 耗時 工作天數；{text}"
        subject, subject_label = "exam", label or "學位考試申請"
        used = memory.get("topic_question_id"); intent = "processing_duration"
    elif sender and last_topic == "exam":
        effective = f"學位考試申請手續 註冊組 送件；{text}"
        subject, subject_label = "exam", label
        used = memory.get("topic_question_id"); intent = "sender"
    elif paper_docs and last_topic == "defer":
        effective = f"論文延後公開 紙本 繳件 佐證文件 裝訂；{text}"
        subject, subject_label = "defer", label
        used = memory.get("topic_question_id"); intent = "paper_documents"
    elif version:
        explicit_format = re.search(r"格式|封面|紙張|纸张|列印|打印|印刷", value) and not re.search(r"延後公開|延后公开", value)
        if (last_topic == "defer" or subject == "defer") and not explicit_format:
            effective = f"論文延後公開 適用版本 文件日期；{text}"; subject, subject_label = "defer", "論文延後公開申請"
        else:
            effective = f"論文格式規定 適用版本 文件日期；{text}"; subject, subject_label = "version", "論文規定適用版本"
        used = memory.get("topic_question_id") if last_topic else None; intent = "version_applicability"
    elif confirmation and last_topic:
        base = _context_base(memory, label)
        effective = f"{base}；確認窗口與官方網頁：{text}"
        subject, subject_label = last_topic, label
        used = memory.get("topic_question_id"); intent = "confirmation"
    elif duration and last_topic == "exam":
        effective = f"學位考試申請 時間 提前送件 審核處理耗時；{text}"
        subject, subject_label = "exam", label
        used = memory.get("topic_question_id"); intent = "duration"
    elif degree or recipient or pages or generic:
        if not last_topic:
            return _friendly("needs_context", "我還不知道這句要接續哪個問題。請補充主題，例如「論文紙本要交幾本？」或「口試怎麼申請？」。", "這句是接著問哪件事？")
        used = memory.get("topic_question_id")
        if degree:
            conditions["degree_level"] = _degree(degree[1]); skips.discard("degree_level"); pending.discard("degree_level")
            effective = f"{_context_base(memory, label, conditions['degree_level'])}；追問：{text}"
            intent = "degree_followup"
        elif recipient:
            if last_topic != "copies":
                return _friendly("needs_context", "你是想問這個單位的論文份數，還是辦理方式／時限？請補上要查的事項。", "請補充要查的事項")
            target = {"注册组": "註冊組", "教务处": "教務處", "图书馆": "圖書館"}.get(recipient[1], recipient[1])
            effective = (f"教務處 註冊組 學位考試成績登錄 修訂完成論文；{text}" if target in ("註冊組", "教務處") else f"論文紙本繳交份數：{target}要交幾本？")
            intent = "recipient"
        elif pages:
            if last_topic != "printing":
                return _friendly("needs_context", "你是在問這個頁數能否單面列印嗎？請補充完整問題，避免把頁數套到其他事項。", "先確認頁數的用途")
            effective = f"論文總共{int(pages[1])}頁，可以單面列印嗎？"; intent = "pages"
        elif generic:
            if value.rstrip("?？。 ").replace("那", "") in ("這個", "这个", "那個", "那个", "它", "怎麼辦", "怎么办", "怎麼做", "怎么做"):
                return _friendly("needs_context", "你指的是哪個辦理事項？請補上名稱或收件單位，我才不會把不同程序混在一起。", "請說明你指的事項")
            effective = f"{_context_base(memory, label)}；追問：{text}"
        subject, subject_label = last_topic, label
    elif sender and not subject:
        return _friendly("needs_context", "你想問的是口試申請、成績登錄，還是離校文件由誰送件？請補上要辦理的事項。", "先確認送件事項")
    if len(effective) > MAX_SEARCH_CHARS:
        return _friendly("needs_context", "接續後的問題超過查詢長度限制，請用一句完整問題重新說明主題與要查的事項。", "請縮短並補全問題")
    return {"response_kind": "normal", "search_text": effective,
            "conditions": conditions, "clarify_skipped": sorted(skips), "pending_conditions": sorted(pending),
            "context_topic": subject, "context_note": f"接續前題：{subject_label}" if used else None,
            "context_question_id": used, "context_intent": intent}


def stage(memory, result, query_plan, now=None):
    now = time.monotonic() if now is None else now
    memory["pending"] = {token: item for token, item in memory["pending"].items()
                         if now - item["at"] <= PENDING_TTL}
    while len(memory["pending"]) >= MAX_PENDING:
        oldest = min(memory["pending"], key=lambda token: memory["pending"][token]["at"])
        del memory["pending"][oldest]
    token = secrets.token_urlsafe(24)
    memory["pending"][token] = {"at": now, "version": memory["version"],
                                 "generation": memory["generation"],
                                 "result": copy.deepcopy(result), "plan": copy.deepcopy(query_plan)}
    return token


def commit(memory, token, question_id, now=None):
    now = time.monotonic() if now is None else now
    receipt = memory["receipts"].get(token)
    if receipt:
        if receipt == question_id:
            return True, None
        return False, "commit_mismatch"
    item = memory["pending"].get(token)
    if not item or now - item["at"] > PENDING_TTL:
        return False, "commit_expired"
    if item["generation"] != memory["generation"] or item["version"] != memory["version"]:
        return False, "conversation_changed"
    if item["result"]["question_id"] != question_id:
        return False, "commit_mismatch"
    projected = memory["turns"] + [item["result"]]
    if len(projected) > MAX_TURNS or len(json.dumps(projected, ensure_ascii=False).encode()) > MAX_HISTORY_BYTES:
        return False, "conversation_limit"
    memory["turns"].append(item["result"])
    plan_ = item["plan"]
    if plan_["response_kind"] in ("normal", "profile_update"):
        memory["conditions"] = copy.deepcopy(plan_["conditions"])
        memory["skipped"] = plan_["clarify_skipped"][:]
        memory["pending_conditions"] = plan_.get("pending_conditions", [])[:]
        if plan_["response_kind"] == "normal":
            memory["topic"] = plan_.get("context_topic")
            memory["topic_question_id"] = question_id if memory["topic"] else None
            memory["topic_search_text"] = plan_.get("search_text") if memory["topic"] else None
    memory["version"] += 1
    memory["receipts"][token] = question_id
    del memory["pending"][token]
    while len(memory["receipts"]) > MAX_TURNS:
        del memory["receipts"][next(iter(memory["receipts"]))]
    return True, None


def public(memory):
    return {"conversation_id": memory["generation"], "version": memory["version"],
            "turns": copy.deepcopy(memory["turns"]),
            "remembered_conditions": copy.deepcopy(memory["conditions"]),
            "clarify_skipped": memory["skipped"][:],
            "pending_conditions": memory.get("pending_conditions", [])[:],
            "memory_scope": "本次服務開啟期間的記憶（1小時未使用即失效；服務重啟或工作階段失效後清除，不寫入磁碟）"}


def allowed_ids(memory):
    return {s["chunk_id"] for turn in memory["turns"] for s in turn.get("sources", [])}
