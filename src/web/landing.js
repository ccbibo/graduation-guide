'use strict';
const EXAMPLES={
  "print": {
    "question": "我的論文只有 60 頁，可以單面列印嗎？",
    "lead": "可以單面，並非必須單面。",
    "rest": "文件的「80 頁以下」包含 60 頁；雙面黑白仍是原則，彩色圖片另有單面例外。",
    "limit": "這是在說明文件內容；你個人的適用年度與條件，仍需確認。",
    "source": "國立清華大學研究生畢業論文格式條例 · 第九點 · PDF 第 2 頁",
    "quote": "採雙\n  面黑白印刷為原則，但頁數為80頁以下時，得以單面印刷（彩色圖片亦可單\n  面印刷）",
    "caption": "固定示例取自已留存的 S04 c003。實際問答可開啟同段原文核對。",
    "chunk_id": "S04#b85cee0260a4152a#ck1#c003"
  },
  "copies": {
    "question": "紙本論文，到底要交幾本？",
    "lead": "圖書館端文件寫二本。",
    "rest": "這不等於所有單位的總份數；系所或學院是否另收，仍須另外確認。",
    "limit": "先分清收件單位，再核對個人適用條件，不能把相近規定直接加總。",
    "source": "國立清華大學研究生畢業論文格式條例 · 第二點 · PDF 第 1 頁",
    "quote": "二、論文份數：\n  應上傳審定完成之論文電子檔內容至圖書館本校博碩士論文庫及提繳完全\n  相同之紙本論文二本至圖書館，一本由圖書館轉送國家圖書館典藏運用、另\n  一本留存本校圖書館典藏運用。\n  各系、所或各學院可自行規定增加份數或要求英文本。",
    "caption": "固定示例取自已留存的 S04 c001。這裡的二本是圖書館端文件內容。",
    "chunk_id": "S04#b85cee0260a4152a#ck1#c001"
  },
  "version": {
    "question": "我是 115 學年度入學，這份規定一定適用我嗎？",
    "lead": "還不能這樣確定。",
    "rest": "入學年是查核線索，不能直接當作文件適用證明。還需要核對版本關係、系所與個人條件。",
    "limit": "沒有核實年度與版本的對應時，不會替你選一份規定當成確定答案。",
    "source": "本專案的適用性限制 · 不是校規條文",
    "quote": "尚未核實入學年度與規章版本的對應關係，不能只憑入學年確定個人適用版本。",
    "caption": "這是原型的回答邊界，不是校方新增規定。實際適用版本請向權責單位確認。",
    "chunk_id": null
  }
};
const tabs=[...document.querySelectorAll('[role="tab"]')];
const panel=document.getElementById('example-panel');
const source=document.getElementById('demo-source');
function showExample(tab,focus=false){
 const key=tab.dataset.example, item=EXAMPLES[key];if(!item)return;
 tabs.forEach(t=>{const active=t===tab;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1;});
 panel.setAttribute('aria-labelledby',tab.id);
 document.getElementById('demo-position').textContent=String(tabs.indexOf(tab)+1).padStart(2,'0')+' / 03';
 document.getElementById('demo-question').textContent=item.question;
 const answer=document.getElementById('demo-answer'),lead=document.createElement('strong');lead.textContent=item.lead;answer.replaceChildren(lead,document.createTextNode(item.rest));
 document.getElementById('demo-limitation').textContent=item.limit;
 document.getElementById('demo-source-title').textContent=item.source;
 document.getElementById('demo-quote').textContent=item.quote;
 document.getElementById('demo-source-caption').textContent=item.caption;
 source.open=false;
 if(focus)tab.focus();
}
tabs.forEach((tab,i)=>{
 tab.addEventListener('click',()=>showExample(tab));
 tab.addEventListener('keydown',e=>{let n=null;if(e.key==='ArrowRight'||e.key==='ArrowDown')n=(i+1)%tabs.length;if(e.key==='ArrowLeft'||e.key==='ArrowUp')n=(i+tabs.length-1)%tabs.length;if(e.key==='Home')n=0;if(e.key==='End')n=tabs.length-1;if(n!==null){e.preventDefault();showExample(tabs[n],true);}});
});
source.addEventListener('toggle',()=>{source.querySelector('.fold-label').textContent=source.open?'收合 ×':'展開 ＋';});
if(tabs.length)showExample(tabs[0]);
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!reduced && 'IntersectionObserver' in window){
 const observer=new IntersectionObserver(entries=>{for(const entry of entries){if(entry.isIntersecting){entry.target.classList.remove('is-pending');observer.unobserve(entry.target);}}},{threshold:.08});
 document.querySelectorAll('.reveal').forEach(section=>{if(section.getBoundingClientRect().top>innerHeight){section.classList.add('is-pending');observer.observe(section);}});
}
