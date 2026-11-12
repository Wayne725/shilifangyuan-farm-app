from copy import deepcopy

from .membership_policy import (
    MEMBERSHIP_ADMISSION_FEE, MEMBERSHIP_SHARE_UNIT_AMOUNT, get_public_membership_quiz,
)


ADMISSION_FLOW_VERSION = "education-review-v1"
MATERIAL_VERSION = "coop-education-2026-10-29.v1"
DECLARATION_VERSION = "admission-volunteer-2026-10-29.v1"
PRIVACY_VERSION = "admission-data-2026-10-29.v1"
PRIVACY_URL = "https://www.coop4sustainability.live/post/有限責任臺灣城鄉永續生活消費合作社個人資料聲明同意書"

_CONFIG = {
    "flow_version": ADMISSION_FLOW_VERSION,
    "material": {
        "version": MATERIAL_VERSION,
        "title": "十里方圓入社教育",
        "url": "https://drive.google.com/file/d/1EQepQHEV4kxItZ0anrsunHt3hmtZIacB/view",
        "sections": [
            {"title": "共同所有與民主管理", "body": "合作社由社員共同參與，採一人一票，不以認股金額決定投票權。"},
            {"title": "合作社七大原則", "body": "自願與公開的社員制、社員的民主管理、社員經濟參與、自治與自立、教育訓練與宣導、社間合作，以及關懷社區。"},
            {"title": "社員經濟參與", "body": "社員認股出資，依合作社規定參與經濟活動；合作社不是以投資獲利為主要目的。"},
            {"title": "在地連結與永續生活", "body": "十里方圓連結在地生產者與社員的永續生活需求，推薦好物與好體驗以聯合國永續發展目標（SDGs）為準則。"},
            {"title": "社員的角色與義務", "body": "社員是出資者、參與者與資源利用者，應與合作社忠實交易，遵守法令章程及大會決議，並參與社務活動。"},
            {"title": "認股意向", "body": "每股股金100元。在校全職學生申請認購5至10股，其他申請人10至100股；學生資格由管理員核驗。填寫認股意向不代表已繳款或取得社員資格。"},
            {"title": "合作社取物站", "body": "第一個合作社取物站於2023年10月3日在清華水木生活空間正式設立。"},
            {"title": "推薦好物", "body": "發現符合永續理念的在地產品時，可透過社員推薦好物的管道提出建議，由合作社評估供應。"},
        ],
    },
    "declaration": {
        "version": DECLARATION_VERSION,
        "privacy_version": PRIVACY_VERSION,
        "privacy_url": PRIVACY_URL,
        "rights": [
            "理、監事及社員代表的選舉權、被選舉權和罷免權。",
            "議事權和決議取消請求權。",
            "大會召集請求權。",
            "書表查閱權。",
            "盈餘分配權。",
            "出社社員退股請求權。",
        ],
        "obligations": [
            "認股繳納股金的義務。",
            "遵守法令章程及大會決議的義務。",
            "與合作社忠實交易的義務。",
            "參加社務活動的義務。",
        ],
        "statement": "本人瞭解「有限責任臺灣城鄉永續生活消費合作社」是一種自助互助的民主經濟組織，也是社員基於實現永續生活的需要而組織的團體。因此，本人願意申請加入貴社，依本次填寫的股數認繳股金，遵守法令及章程規定，履行各項權利義務，敬請許可入社。",
        "admission_fee_amount": MEMBERSHIP_ADMISSION_FEE,
        "admission_fee_version": 0,
        "share_unit_amount": MEMBERSHIP_SHARE_UNIT_AMOUNT,
        "student_min_shares": 5,
        "student_max_shares": 10,
        "general_min_shares": 10,
        "general_max_shares": 100,
    },
    "discovery_channels": [
        "合作社籌備會", "合作社說明會", "本合作社Facebook", "媒體報道", "朋友介紹",
        "宣傳海報", "校務資訊系統", "本合作社官方網站", "Instagram", "合作社Email通知", "其他",
    ],
}


def get_admission_config() -> dict:
    config = deepcopy(_CONFIG)
    quiz = get_public_membership_quiz()
    config["quiz"] = {
        **quiz,
        "questions": [
            {**question, "options": [{"value": key, "label": label} for key, label in question["options"].items()]}
            for question in quiz["questions"]
        ],
    }
    return config
