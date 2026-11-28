"""近似重复的岗位：同一个岗位换了个名字，或者在几个平台各发了一次。

链接或 JD 完全相同的，保存时已经合成一条（app/db/jobs.py）。这里找的是「看起来是同一个」的：
公司名归一后相同、城市不冲突、职位名很像，有 JD 时 JD 也大部分相同。只标出来（similar_to 指向
更早的那个），不合并、不删除：判错了会漏掉真岗位，由你自己决定。

阈值是按岗位重复与名称相近的正反样例定的（同岗位改名重发、跨平台转载、同公司
不同岗位、同名不同 JD），没有用大量真实数据校准，用下来误判多再调。
"""

import re
import unicodedata
from difflib import SequenceMatcher

from sqlmodel import Session, col, select

from app.db.models import Job

TITLE_SIMILAR = 0.8  # 都有 JD 时，职位名的相似度
TITLE_ALMOST_SAME = 0.9  # 有一边没有 JD 时，只能靠职位名，要求更像
JD_OVERLAP = 0.5  # JD 按三个字一组切开后，重合的比例

_BRACKETS = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_NOISE = re.compile(r"[\s\W_]+")
# 公司名里不区分公司的部分：地区、组织形式、常见的行业词
_COMPANY_WORDS = re.compile(
    r"有限责任公司|股份有限公司|有限公司|分公司|集团|控股|股份|公司|"
    r"科技|技术|网络|信息|软件|智能|数据|"
    r"北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|天津|重庆"
)
_TITLE_WORDS = re.compile(r"急招|急聘|直招|高薪|双休|五险一金|实习生?|校招|社招")


def _plain(text: str) -> str:
    return _NOISE.sub("", unicodedata.normalize("NFKC", text or "")).lower()


def company_key(name: str) -> str:
    return _COMPANY_WORDS.sub("", _plain(_BRACKETS.sub("", name or "")))


def title_key(title: str) -> str:
    text = _BRACKETS.sub("", unicodedata.normalize("NFKC", title or ""))
    return _plain(_TITLE_WORDS.sub("", text)).removeprefix("ai")


def _same_company(a: str, b: str) -> bool:
    a, b = company_key(a), company_key(b)
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return len(short) >= 2 and short in long


def _city_conflicts(a: str | None, b: str | None) -> bool:
    a, b = _plain(a or ""), _plain(b or "")
    return bool(a and b and a[:2] != b[:2])


def _grams(text: str) -> set[str]:
    plain = _plain(text)
    return {plain[i : i + 3] for i in range(len(plain) - 2)}


def jd_overlap(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    return len(ga & gb) / min(len(ga), len(gb)) if ga and gb else 0.0


def looks_like_same_job(a: dict, b: dict) -> bool:
    """a、b 是有 company、title、city、jd_text 的字典或对象属性。"""
    if not _same_company(a["company"], b["company"]):
        return False
    if _city_conflicts(a.get("city"), b.get("city")):
        return False
    if "实习" in (a["title"] + b["title"]) and ("实习" in a["title"]) != ("实习" in b["title"]):
        return False
    title = SequenceMatcher(None, title_key(a["title"]), title_key(b["title"])).ratio()
    if a["jd_text"].strip() and b["jd_text"].strip():
        return title >= TITLE_SIMILAR and jd_overlap(a["jd_text"], b["jd_text"]) >= JD_OVERLAP
    return title >= TITLE_ALMOST_SAME


def _fields(job: Job) -> dict:
    return {"company": job.company, "title": job.title, "city": job.city, "jd_text": job.jd_text}


def find_similar(session: Session, job: Job) -> int | None:
    """比它早的、没删除的岗位里，最早一个看起来是同一岗位的编号。"""
    if not company_key(job.company):
        return None
    earlier = session.exec(
        select(Job).where(Job.deleted_at == None, col(Job.id) < job.id).order_by(col(Job.id))  # noqa: E711
    ).all()
    return next(
        (other.id for other in earlier if looks_like_same_job(_fields(job), _fields(other))), None
    )
