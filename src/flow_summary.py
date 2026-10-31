"""Small curated summaries. Claims require an issued chunk and exact text proof.

Question matching selects presentation only. It never supplies a school fact.
NFKC/whitespace normalization is only used to locate a span; citations retain
the original Unicode characters and whitespace from the authoritative chunk.
"""
from __future__ import annotations

import re
import unicodedata
from scope_aliases import canonical_dept

VERSION = "curated-evidence-summary-3"


def exact_quote(text: str, needle: str):
    chars, offsets = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            if not normalized.isspace():
                chars.append(normalized)
                offsets.append(index)
    target = "".join(unicodedata.normalize("NFKC", needle).split())
    at = "".join(chars).find(target)
    if not target or at < 0:
        return None
    start, end = offsets[at], offsets[at + len(target) - 1] + 1
    return {"quote": text[start:end], "char_offset_in_chunk": start, "char_length": end-start}


def _topic(query):
    if re.search("版本|最新版|最新|現行|哪一年|哪年|哪個學年度|[0-9]{2,4}學年度.*(?:呢|版本|適用)|適用.*(?:年|版)|入學.*(?:版|彙輯|適用)|[0-9]{4}年.*入學.*適用", query):
        return "version"
    if re.search("門禁|論文建檔|袍服", query) and re.search("借書|還書|滯還金", query):
        return "campus_flow"
    if re.search("門禁|論文建檔", query):
        return "department_tasks"
    if re.search("借的書|借書|還書|滯還金|歸還.*書", query):
        return "library_returns"
    if re.search("延後|延遲|延緩|公開", query):
        return "delay"
    if re.search("申請.*(口試|學位考試)|(口試|學位考試).*申請|由誰.*(?:送|註冊)|誰.*送件|註冊組.*(?:審|工作天|耗時)|(?:審核|審好|審完).*(?:多久|天|時間)", query):
        return "exam"
    if re.search("全校|跨單位|離校.*(?:第一步|順序|完整流程|待辦)|完整.*離校|先.*(?:圖書館|註冊組).*再|沒有.*先後順序", query):
        return "campus_flow"
    if re.search("單面|雙面|列印|印刷|紙張|頁|封面|顏色|格式|裝訂", query):
        return "format"
    if re.search("幾本|幾份|份數|本數|紙本|繳.*論文|交.*論文|註冊組|教務處|圖書館", query):
        return "copies"
    return "other"


def _rule(key, selectors, needles, title, text, version=None):
    return {"id": key, "selectors": selectors, "needles": needles,
            "title": title, "text": text, "version_tag": version}


LIBRARY = _rule("library-two", [("S04", "c001"), ("S03", "c006"), ("S02", "c004"), ("S05", "c003")],
    [["紙本論文二本至圖書館"], ["紙本論文二本至本校圖書館"], ["繳交論文二本。兩本論文繳交至總圖書館後"]],
    "圖書館這一份：紙本二本", "圖書館端文件寫二本紙本論文；這個數字不等於所有收件單位的總份數。")
OFFICE = _rule("office-one", [("S07", "c002")],
    [["各系、所、學位學程應於次學期註冊日前將各研究生及格之論文考試成績、考試委員審定書影印本及修訂完成之論文一本一併送交教務處登錄"]],
    "系所送教務處：另附修訂完成論文一本", "這份碩士規定由系所送件登錄，併送修訂完成論文一本。")
SAME_FILE = _rule("same-file", [("S04", "c001")],
    [["應上傳審定完成之論文電子檔內容至圖書館本校博碩士論文庫及提繳完全相同之紙本論文二本至圖書館"]],
    "準備內容一致的電子檔與紙本", "文件要求上傳審定完成的電子檔，並提繳內容完全相同的紙本；本句未指定兩者的先後。")
LOCATION = _rule("library-location", [("S05", "c005")], [["繳交地點：總圖書館4號、5號櫃檯"]],
    "圖書館繳交地點", "繳交地點為總圖書館 4 號、5 號櫃檯。")
EXTRA = _rule("department-extra", [("S04", "c001")], [["各系、所或各學院可自行規定增加份數或要求英文本"]],
    "系所是否另有要求", "系所或學院可增加份數或要求英文本，個人總份數仍需確認。")
PRINT = _rule("printing-branches", [("S04", "c003")],
    [["採雙面黑白印刷為原則，但頁數為80頁以下時，得以單面印刷（彩色圖片亦可單面印刷）"]],
    "依頁數與圖片選列印方式", "雙面黑白是原則；80 頁以下得單面印刷，彩色圖片也可單面印刷。")
PAPER = _rule("paper-size", [("S04", "c003")],
    [["論文除封底面用150磅模造紙外，均採用白色A4尺寸80磅白色模造紙紙張（影印紙）為原則"]],
    "紙張規格", "文件原則採白色 A4、80 磅模造紙，封底面用 150 磅模造紙。")
FOLD = _rule("fold-a4", [("S04", "c003")], [["有跨頁需求時，可自行調整紙張大小，惟印製成紙本論文時，應摺疊成A4尺寸"]],
    "有跨頁內容時", "跨頁需求可調整紙張大小，印成紙本後須摺疊成 A4 尺寸。")
COLOR = _rule("cover-colors", [("S04", "c002")], [["碩士論文為米黃色、博士論文為淺藍色，上光膠裝"]],
    "封面依學位別分流", "碩士論文為米黃色，博士論文為淺藍色，採上光膠裝。")
DELAY_0529 = _rule("delay-0529", [("S02", "c005")],
    [["但涉及機密、專利事項或依法不得提供者，應檢附證明文件並經指導教授與系所主管認定，學位論文始得申請延後公開"]],
    "2025-05-29 版：證明與認定要求", "涉及機密、專利或依法不得提供時，須檢附證明文件，並經指導教授與系所主管認定。", "114.5.29")
DELAY_1023 = _rule("delay-1023", [("S03", "c007")],
    [["論文如涉及機密、專利事項或依法不得提供者，學生應於申請學位考試時提出論文延後公開申請，並依本校「學位論文延後公開處理辦法」辦理"]],
    "2025-10-23 版：於申請學位考試時提出", "涉及機密、專利或依法不得提供時，該版寫明於申請學位考試時提出，並依延後公開處理辦法辦理。", "114.10.23")
DELAY_PAPER = _rule("delay-paper-enclosure", [("S05", "c004")],
    [["若申請延後公開，請將延後公開申請書與佐證資料正本，與二本論文一併繳交至總", "圖書館（不須裝訂）"]],
    "繳件時的延後公開文件", "此繳件說明要求延後公開申請書、佐證資料正本與二本論文一併繳交，申請書及佐證資料不須裝訂。")
EXAM = [
    _rule("exam-platform", [("S07", "c001")], [["請先至校務資訊系統--研究生學位考試平台申請後印出相關表件"]],
          "先在平台申請，再印出表件", "先至校務資訊系統的研究生學位考試平台申請，再印出相關表件。"),
    _rule("exam-documents", [("S07", "c001")], [["應檢具論文初稿、歷年成績單正本、碩士學位考試申請表等表件向系(所)辦公室提出申請", "經指導教授及系主任(所長)簽字同意後"]],
          "備齊申請資料與簽字同意", "備論文初稿、歷年成績單正本、申請表等資料向系所辦公室申請，並取得指導教授與系所主管簽字同意。"),
    _rule("exam-office-deadline", [("S07", "c001")], [["經指導教授及系主任(所長)簽字同意後，由系(所)辦公室將碩士學位考試申請表，於口試日期二週前送註冊組辦理，逾期不受理"]],
          "由系所辦公室在口試二週前送件", "簽字同意後，由系所辦公室於口試日期二週前把申請表送註冊組。"),
    _rule("exam-approval", [("S07", "c001")], [["碩士學位考試申請及考試委員資格，經審核無異後，由註冊組經由研究生學位考試平台email通知系(所)，始可舉行學位考試"]],
          "資格審核通過並通知系所後，才可舉行", "申請與考試委員資格經審核無異後，註冊組經平台通知系所，始可舉行學位考試。"),
]


CAMPUS_TASKS = [
    _rule("campus-department", [("S01", "c002")],
          [["學生應依個別情況完成校內其它單位應完成之手續", "系所辦公室門禁設定、論文建檔"]],
          "系所：門禁設定與論文建檔", "依個別情況向系所辦公室確認門禁設定、論文建檔手續。"),
    _rule("campus-library", [("S01", "c002")],
          [["學生應依個別情況完成校內其它單位應完成之手續", "圖書館借書歸還或滯還金繳清"]],
          "圖書館：歸還借書或繳清滯還金", "有借書或滯還金時，處理歸還或繳清事項；本片段未列歸還方式、金額或期限。"),
    _rule("campus-robes", [("S01", "c002")],
          [["學生應依個別情況完成校內其它單位應完成之手續", "保管組借用袍服歸還"]],
          "有借袍服時：確認歸還", "借用袍服者對應保管組歸還事項，不表示每位學生都須辦理。"),
]
CAMPUS_ANNOUNCEMENTS = _rule("campus-announcements", [("S01", "c002")],
    [["依各權責單位網頁公告事項辦理"]], "查各權責單位公告", "細節依各權責單位網頁公告辦理；本次未取得那些公告頁的具體連結。")
CERTIFICATE_ANNOUNCEMENT = _rule("certificate-announcement", [("S01", "c001")],
    [["領取學位證書注意事項、時間、地點，由教務處註冊組公告於註冊組網頁"]],
    "領證細節查註冊組公告", "領取學位證書的注意事項、時間與地點由註冊組公告；本次未取得該公告頁連結。")


def _printing_answer(question):
    """Use an explicit positive total before negated ranges or color-page counts."""
    base = "雙面黑白是原則；80 頁以下得單面印刷，彩色圖片也可單面印刷。"
    appendix_note = " 本次片段未界定正文、附錄的頁數計算範圍；可向系辦或圖書館格式承辦確認附錄、前置頁是否計入 80 頁。"
    total_pattern = r"(?:全文(?:含附錄)?|含附錄|全本|整本|總頁數|總共|總計|合計|論文)(?:\s*(?:含附錄|為|是|共|總共|剛好|只有|有))*\s*(\d{1,4})\s*頁"
    totals = [m for m in re.finditer(total_pattern, question) if not re.search("不是|並非|不算", question[max(0,m.start()-4):m.start()]) and not re.match(r"\s*(?:以上|以下|以內|以外|左右|上下|多|少)", question[m.end():])]
    total = totals[-1] if totals else None
    counting_question = re.search("(?:全部|所有|計算|計入|包含|包括).*(?:頁數|附錄)|頁數.*(?:怎麼算|如何算|全部)|正文.*附錄|正文.*附录", question)
    ranges = [m for m in re.finditer(r"(?:80|八十)\s*頁以上", question) if not re.search("不是|並非|不屬於|不是指", question[max(0,m.start()-6):m.start()])]
    if total is None and ranges:
        return "「80 頁以上」要分開看：剛好 80 頁屬於「80 頁以下」，可以單面；超過 80 頁不在這項頁數例外，原則雙面黑白，彩色圖片仍可單面。"
    numbers = re.findall(r"(?<!\d)(\d{1,4})\s*頁", question)
    ambiguous = re.search("不是|並非|不確定|至少|至多|超過|不到|少於|大於|以下|以內|以外|左右|大約|約|[-~～—–]", question)
    count = int(total[1]) if total else (int(numbers[0]) if len(numbers)==1 and not ambiguous and not counting_question else None)
    if len(numbers)>1 and re.search("正文|附錄|附录", question) and not (total and re.match("全文|含附錄|全本|整本|總頁數|總共|總計|合計", total.group(0))):
        count = None  # a body count plus appendix count is not an explicit total
    if count is None:
        if counting_question or re.search("正文|附錄|附录|全部頁數", question):
            return "本次原文未界定正文、附錄或全部頁數如何計入 80 頁，不能只拿正文頁數判定符合。" + base + appendix_note
        return base
    if count <= 80:
        if re.search("必須|一定要|就要", question):
            answer = f"不是必須；{count} 頁可對照「80 頁以下」分支選擇單面。原文的「得」是允許，雙面黑白仍是原則。"
        else:
            answer = f"{count} 頁屬於文件的「80 頁以下」分支，可以單面（原文「得以單面印刷」），並非必須單面。"
    else:
        answer = f"{count} 頁不在「80 頁以下」的頁數例外，原則採雙面黑白；彩色圖片仍可單面印刷。"
        if re.search("彩色|彩圖", question) and re.search("其餘|其余|剩下|這.*頁|那.*頁", question):
            answer += " 依這兩項條文原則，彩圖部分單面、其餘雙面黑白的方向相符；具體混印編排仍需向系辦或圖書館確認。"
    return answer + (appendix_note if re.search("附錄|附录|正文", question) else "")


def _eligible(row, conditions, excluded):
    if row["chunk_id"] in excluded:
        return False
    applies = row.get("applies") or {}
    degree = conditions.get("degree_level")
    if degree and applies.get("degree_level") and degree not in applies["degree_level"]:
        return False
    dept = canonical_dept(conditions.get("dept"))
    markers = [x.split(":", 1)[1].strip() for x in applies.get("scope", []) if x.startswith("系所:")]
    if dept and markers and not any(x == dept or x in dept or dept in x for x in markers):
        return False
    return True  # unknown academic-year semantics are never guessed here


def build_flow(query, result, corpus, excluded_ids=()):
    issued = result.get("sources") or []
    by_id = corpus.by_id if hasattr(corpus, "by_id") else corpus
    conditions = result.get("conditions") or {}
    allowed = {}
    for src in issued:
        cid = src.get("chunk_id")
        row = by_id.get(cid)
        if not row or src.get("source_id") != row["source_id"] or not _eligible(row, conditions, set(excluded_ids)):
            continue
        anchor = src.get("quote_anchor")
        if anchor and anchor not in row["text"]:
            continue
        allowed[cid] = row

    def match(rule):
        evidence = []
        for sid, ordinal in rule["selectors"]:
            for cid, row in allowed.items():
                if row["source_id"] != sid or cid.rsplit("#", 1)[-1] != ordinal:
                    continue
                if rule["version_tag"] and row.get("version_tag") != rule["version_tag"]:
                    continue
                for group in rule["needles"]:
                    spans = [exact_quote(row["text"], needle) for needle in group]
                    if all(spans):
                        evidence.extend({"chunk_id": cid, "source_id": sid, **span} for span in spans)
                        break
        if not evidence:
            return None
        return {"claim_id": rule["id"], "title": rule["title"], "text": rule["text"], "evidence": evidence}

    question = result.get("query_text") or query
    topic = _topic(question)
    contextual_topic = _topic(query)
    if topic == "other":
        topic = contextual_topic
    # A paper-enclosure follow-up can retain the prior delay topic without
    # copying an old answer or treating enclosure as the initial application.
    delay_documents = bool(re.search("申請書|佐證", question) and (re.search("延後|延遲|公開", query) or "佐證" in question))
    paper_enclosure = bool(re.search("繳.*紙本|紙本.*(?:帶|文件|準備)|繳件.*(?:文件|帶|準備)", question)) or delay_documents
    if (paper_enclosure and re.search("延後|延遲|公開", query)) or delay_documents:
        topic = "delay"
    claims, sequence, notes = [], [], []
    summary = "這次尚不能整理出有依據的辦理摘要，可展開下方命中來源查核。"
    summary_evidence = []
    lookup_guidance = []
    degree = conditions.get("degree_level")
    self_delivery = bool(re.search("學生.*(?:自己|自行).*送|(?:自己|自行|本人).*(?:送|拿去)|代送", question))
    review_duration = bool(re.search("審.*(?:多久|幾|天|時間|耗時)|幾(?:個)?工作天|處理.*(?:多久|幾天)", question))
    replacement = bool("圖書館" in question and re.search("註冊組|教務處", question) and re.search("不用|不必|免|抵|取代|代替|只要", question))
    office_target = "註冊組" if "註冊組" in question else ("教務處" if "教務處" in question else None)
    both_recipients = bool("圖書館" in question and re.search("註冊組|教務處", question) and re.search("各|分別|總共|是不是|寫錯|到底|不同", question)) or replacement
    if both_recipients:
        office_target = None
    if not office_target and topic == "copies" and "圖書館" not in question:
        office_target = "註冊組" if "註冊組" in query else ("教務處" if "教務處" in query else None)
    library_target = "圖書館" in question and not office_target and not both_recipients
    if topic == "campus_flow":
        summary = "本次來源未能核實跨單位完整順序；可先看各單位有據的無序待辦，不代表全部適用你。"
    elif topic == "library_returns":
        summary = "本次未取得借書歸還或滯還金規範，不能用論文繳本內容回答借書。可查圖書館借還書／離校公告；該公告頁連結本次未取得。"
    elif topic == "version":
        summary = "不能只因文件日期較新就決定適用版本；入學年與適用版本的對應尚未核實，不能直接推定。"
    elif topic == "department_tasks":
        summary = "本次未取得系所門禁或論文建檔手續的原文，可向系所辦公室確認；不能據此判定個人是否完成。"
    elif topic == "exam":
        summary = "本次未取得可確認口試申請送件或審核時間的原文，請向系辦或註冊組確認。"
        if review_duration:
            summary = "本次未取得註冊組審核工作天數的明文，無法確認多久審好；可向註冊組確認審核耗時。"
        elif self_delivery:
            summary = "本次未取得學生可否自行或代為送件的明文，不能判定允許或禁止；可向系辦或註冊組確認。"
    elif office_target and topic == "copies":
        summary = f"本次未取得足以確認{degree or '你所問學位'}在{office_target}應交份數的原文，不能用圖書館份數代答。"
    if result.get("decision") == "C" or not issued:
        targeted = topic in {"campus_flow", "library_returns", "department_tasks", "version", "exam"} or office_target
        return {"version": VERSION, "kind": "no_match", "summary": {"text": summary if targeted else "本次沒有可引用來源，無法列出辦理步驟。", "evidence": []}, "items": [], "local_sequence": [], "checks": [], "uncertainties": []}
    if topic == "copies":
        lib, office = match(LIBRARY), match(OFFICE)
        if office_target:
            claims = [x for x in (office, lib) if x]
            if office:
                summary = f"{office_target}這一端，本次碩士規定寫由系所併送修訂完成論文一本登錄；個人適用仍須核對。"
                if not degree:
                    summary = "你的學位尚未確認，以下只是碩士文件分支：" + summary
                summary_evidence = office["evidence"]
            elif lib:
                lib["title"] = "對照資訊：圖書館的二本要求"
                summary += " 以下圖書館二本僅作另一收件端的對照。"
                summary_evidence = lib["evidence"]
        elif library_target:
            claims = [x for x in (lib, match(LOCATION), match(SAME_FILE)) if x][:4]
            if lib:
                summary = f"圖書館端文件寫二本紙本論文{('；這裡按你明填的'+degree+'條件列出文件內容') if degree else ''}。系所是否另加收，仍須另外確認。"
                summary_evidence = lib["evidence"]
        else:
            claims = [x for x in (lib, office, match(LOCATION), match(SAME_FILE)) if x][:4]
            if lib and office:
                summary = "圖書館端文件寫二本；系所送教務處另附一本。兩者收件單位與用途不同。"
            elif lib:
                summary = "圖書館端文件寫二本；其他收件單位是否另收，仍須另外確認。"
            elif office:
                summary = "本次片段寫的是系所送教務處的修訂完成論文一本，不能當成圖書館份數。"
            summary_evidence = [e for x in (lib, office) if x for e in x["evidence"]]
        if replacement:
            claims = [x for x in (lib, office) if x]
            if lib and office:
                summary = "本次取得的是不同收件單位的兩項要求：圖書館二本、系所送教務處一本；沒有可互抵或相互取代的依據。"
                summary_evidence = lib["evidence"] + office["evidence"]
            elif lib:
                summary = "本次能確認圖書館文件寫二本；教務處『一本』與能否互抵尚缺本次原文，不能據此說圖書館不用交。"
                summary_evidence = lib["evidence"]
            elif office:
                summary = "本次取得的是系所送教務處的一本要求；圖書館端與是否可互抵仍待取得對應原文，不能由一本推出免交圖書館。"
                summary_evidence = office["evidence"]
            else:
                summary = "本次沒有足夠原文確認兩單位份數或互抵關係，不能據此判定免交任一端。"
        elif office_target and self_delivery:
            if office:
                summary = "本次成績登錄規定寫由系所併送教務處；是否允許學生自行或代送未明，不能據此判定允許或禁止。"
                summary_evidence = office["evidence"]
            else:
                summary = f"本次未取得可確認{office_target}這份文件的送件人、學生可否自行送的原文；可向系辦或{office_target}確認。"
                summary_evidence = []
        extra = match(EXTRA)
        if extra:
            notes.append(extra)
    elif topic == "format":
        color_topic = bool(re.search("封面|顏色|書背", question)) or (not re.search("單面|雙面|列印|印刷|紙張|頁", question) and bool(re.search("封面|顏色|書背", query)))
        rules = [COLOR] if color_topic else [PRINT]
        if re.search("紙張|紙質|A4|磅", question):
            rules.append(PAPER)
        if re.search("跨頁|摺|折", question):
            rules.append(FOLD)
        if "格式" in question and not color_topic:
            rules.extend([PAPER, FOLD, COLOR])
        claims = [x for rule in rules if (x := match(rule))][:4]
        printable = next((x for x in claims if x["claim_id"] == "printing-branches"), None)
        colored = next((x for x in claims if x["claim_id"] == "cover-colors"), None)
        if printable:
            summary, summary_evidence = _printing_answer(question), printable["evidence"]
        elif colored:
            summary, summary_evidence = colored["text"], colored["evidence"]
            if degree == "博士":
                summary = "依本次文件的博士分支，封面是淺藍色、上光膠裝；個人適用仍待核實。"
                colored["text"] = "博士分支為淺藍色、上光膠裝；碩士米黃色是另一學位分支，僅作對照。"
                colored["title"] = "你明填的博士分支"
            elif degree == "碩士":
                summary = "依本次文件的碩士分支，封面是米黃色、上光膠裝；個人適用仍待核實。"
                colored["text"] = "碩士分支為米黃色、上光膠裝；博士淺藍色是另一學位分支，僅作對照。"
                colored["title"] = "你明填的碩士分支"
    elif topic == "version":
        calendar_year = re.search(r"([0-9]{4})年.*入學", question)
        if calendar_year:
            summary = f"你提到的 {calendar_year[1]} 年入學是日曆年線索，不能直接換成入學學年度或適用版本。" + summary
        # Dates are provenance metadata, not school-rule claims or proof of
        # applicability. A quote below identifies only the cited document.
        format_version = bool(re.search("格式|封面|印刷|列印|顏色", question))
        relevant_sources = [src for src in issued if src.get("chunk_id") in allowed and (not format_version or src["source_id"] == "S04")]
        seen = set()
        for src in relevant_sources:
            sid = src["source_id"]
            if sid in seen:
                continue
            seen.add(sid)
            row = allowed[src["chunk_id"]]
            quote = src.get("quote_anchor") or row["text"]
            at = row["text"].find(quote)
            date = src.get("document_date")
            version = row.get("version_tag")
            date_label = date if date and date != "unknown" else ("版次 " + version if version else "未標示")
            evidence = [{"chunk_id": row["chunk_id"], "source_id": sid, "quote": quote, "char_offset_in_chunk": at, "char_length": len(quote), "evidence_role": "document_identity_only"}]
            claims.append({"claim_id": "version-record-"+sid, "title": src.get("title") or row["doc_title"],
                           "text": f"本次文件日期記錄：{date_label}。這個日期不證明它對你適用。", "evidence": evidence,
                           "metadata_basis": {"field": "document_date", "value": date, "version_tag": version}})
        claims = claims[:4]
        summary_evidence = [e for x in claims for e in x["evidence"]]
        summary += " 目前缺的是校方對適用對象、學年度或取代關係的明示說明；可向系所或教務處確認。"
        if format_version and not claims:
            summary += " 本次未取得格式文件的日期資料。"
    elif topic == "delay":
        earlier, later, enclosure = match(DELAY_0529), match(DELAY_1023), match(DELAY_PAPER)
        if paper_enclosure:
            claims = [enclosure] if enclosure else [x for x in (earlier, later) if x]
            if enclosure:
                if re.search("影本|影印|複印|掃描", question):
                    summary = "本次繳件說明寫的是佐證資料正本；影本能否替代未明，不能直接認定影本符合，請向圖書館確認。"
                elif re.search("裝訂|訂在一起|一起訂", question):
                    summary = "申請書與佐證資料不須裝訂；這句指的是這兩類文件，不能延伸成論文本身也不用裝訂。"
                else:
                    summary = "繳紙本這個階段，本次說明寫：延後公開申請書、佐證資料正本與二本論文一併繳交；申請書及佐證資料不須裝訂。"
                summary_evidence = enclosure["evidence"]
            else:
                if re.search("影本|影印|複印|掃描", question):
                    summary = "本次未取得佐證資料正本／影本替代的繳件規定，不能確認影本是否可接受。下列僅保留本次取得的申請條文對照。"
                elif re.search("裝訂|訂在一起|一起訂", question):
                    summary = "本次未取得申請書與佐證資料的裝訂規定，不能判定是否與論文一起裝訂。下列僅保留本次取得的申請條文對照。"
                else:
                    summary = "本次未取到繳紙本時應帶的延後公開申請書／佐證資料清單，不能從首次提出時點推算攜帶文件。下列僅保留本次取得的申請條文對照。"
                summary_evidence = []
        else:
            claims = [x for x in (earlier, later, enclosure) if x]
            if earlier and later:
                summary = "兩版寫法不同：2025-10-23 版提到申請學位考試時；2025-05-29 版列證明與認定要求。"
            elif later:
                summary = "本次命中 2025-10-23 版：於申請學位考試時提出延後公開申請。是否適用你仍須確認。"
            elif earlier:
                summary = "本次命中 2025-05-29 版：備證明並經指導教授與系所主管認定；本片段未列唯一提出日期。"
            summary_evidence = [e for x in (earlier, later) if x for e in x["evidence"]]
    elif topic == "exam":
        sequence = [x for rule in EXAM if (x := match(rule))]
        deadline = next((x for x in sequence if x["claim_id"] == "exam-office-deadline"), None)
        duration = review_duration or bool(re.search("要多久|幾天|審核.*(?:多久|時間|耗時)|多久.*審核", question))
        sender = bool(re.search("由誰|誰.*送|送件|提前|幾週|幾周", question))
        if deadline and self_delivery:
            summary = "本次原文指定由系所辦公室送註冊組；是否允許學生自行或代送未明，不能據此禁止或允許，需向系辦或註冊組確認。"
            summary_evidence = deadline["evidence"]
        elif deadline and review_duration:
            summary = "本次片段未明示註冊組審核需要幾個工作天；它只給『口試二週前由系辦送件』的提前量，不能當成審核耗時。"
            summary_evidence = deadline["evidence"]
        elif deadline and duration:
            summary = "若問送件提前量，本次規定是由系所辦公室於口試日期二週前送註冊組；若問審核要幾天，本次片段未明示，兩者不能混為審核耗時。"
            summary_evidence = deadline["evidence"]
        elif deadline and sender:
            summary = "由系所辦公室將申請表送註冊組，須在口試日期二週前送達；本次來源限這份碩士申請規定。"
            summary_evidence = deadline["evidence"]
        elif len(sequence) == len(EXAM):
            summary = "這份碩士申請規定列出：平台申請、備件與簽同意、系辦送註冊組、審核通知後舉行考試。"
            summary_evidence = [e for x in sequence for e in x["evidence"]]
        elif (sender or duration) and not review_duration and not self_delivery:
            summary = "本次未取到可確認口試送件人或提前時間的原文，不能據此回答由誰送件、需幾天。"
        if len(sequence) != len(EXAM):
            claims, sequence = sequence, []
    elif topic in {"campus_flow", "library_returns", "department_tasks"}:
        candidates = [CAMPUS_TASKS[1]] if topic == "library_returns" else ([CAMPUS_TASKS[0]] if topic == "department_tasks" else CAMPUS_TASKS)
        claims = [x for rule in candidates if (x := match(rule))]
        announcement = match(CAMPUS_ANNOUNCEMENTS)
        certificate = match(CERTIFICATE_ANNOUNCEMENT)
        if topic == "library_returns" and claims:
            summary = "本次離校文件列有圖書館借書歸還或滯還金繳清事項；有借書或欠款時應對應處理，但本片段未給方式、金額或期限。"
            summary_evidence = claims[0]["evidence"]
            if re.search("滯還金|欠款", question) and re.search("完成|辦完|算.*(?:好|完)|狀態", question):
                summary = "目前不能確認圖書館離校已完成；文件列有滯還金繳清事項，你仍有欠款時請向圖書館核對繳清與個人離校狀態。本片段未給金額、期限或核銷方式。"
            elif re.search("滯還金.*(?:怎麼|如何|哪裡|管道)", question):
                summary = "本次文件只列滯還金繳清事項，未列繳款管道、金額或期限；請向圖書館確認個人欠款與可用繳款方式。"
        elif topic == "department_tasks" and claims:
            if "門禁" in question:
                summary = "門禁設定這項可向系所辦公室確認；本次無法查你的個人完成狀態，也不推定全校先後。"
                claims[0]["title"] = "門禁事項：確認系所辦公室"
                claims[0]["text"] = "本次來源將門禁設定列在系所辦公室手續中，請向系辦確認未完成項目與個人狀態。"
            else:
                summary = "論文建檔事項可向系所辦公室確認；本次未取得個人完成狀態。"
            summary_evidence = claims[0]["evidence"]
        elif topic == "campus_flow" and claims:
            summary = "本次來源未能核實跨單位完整先後；可整理系所、圖書館或借袍服等已命中的無序待辦，依個別情況辦理。"
            summary_evidence = [e for x in claims for e in x["evidence"]]
        if announcement:
            notes.append(announcement)
        if certificate and re.search("領證|領.*證書|哪個.*(?:單位|網頁)|公告|確認", question):
            claims.append(certificate)
        if not claims:
            direction = "可分別查圖書館繳件與註冊組離校／領證公告；本次未取得兩者先後規範或公告頁連結。" if "註冊組" in question else "可查圖書館借還書／離校公告或系所離校說明；這是查詢方向，本次未取得相應公告頁連結。"
            lookup_guidance.append({"text": direction, "guidance_only": True})
    checks = [{"slot": q["slot"], "text": q["question"]} for q in result.get("clarify_questions", [])]
    cited = {e["chunk_id"] for x in claims+sequence+notes for e in x.get("evidence", [])}
    related, unrelated = [], []
    for conflict in result.get("conflicts", []):
        group = conflict.get("group") or conflict.get("id") or "unknown"
        members = set(conflict.get("dual_listed_chunk_ids", [])) | set(conflict.get("hit_chunk_ids", []))
        (related if members & cited and (topic == "version" or (topic == "delay" and not paper_enclosure)) else unrelated).append(group)
    return {"version": VERSION, "kind": topic, "summary": {"text": summary, "evidence": summary_evidence},
            "items": claims, "local_sequence": sequence, "checks": checks, "uncertainties": notes,
            "lookup_guidance": lookup_guidance,
            "formatter_notes": {"related_conflict_groups": related, "unrelated_conflict_groups": unrelated,
                                "original_decision_unchanged": result.get("decision")},
            "limitation": "依本次取得文件整理；個人適用條件與年度對應仍待核實。" + (" 兩版如有命中則並列，不代你選版。" if topic == "delay" and not paper_enclosure else "")}
