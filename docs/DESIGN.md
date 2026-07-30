# 十里方圓視覺規格

## 設計定位

- 平台：iOS／Android 跨平台中性
- 類型：生活消費、社務、活動與現場服務
- 氛圍：台灣產地、溫暖、可信任、帶有紙張與竹籃的自然質感
- 結構：圖片帶動瀏覽，訂單與管理頁則保持清楚、扁平

## Design Bible

| 項目 | 規格 |
|---|---|
| 主色 | 深林綠 `#173F35` |
| 行動色 | 柿橘 `#E35E2F` |
| 背景 | 暖米白 `#F5F0E6` |
| 輔助色 | 鼠尾草綠 `#A9B5A0` |
| 文字 | 炭黑 `#252B28` |
| 字體方向 | 系統繁體中文字體、清楚的人文無襯線 |
| 間距 | 8pt 基準 |
| 圓角 | 10／16／22px，避免所有元素都膠囊化 |
| 圖像 | 竹籃、亞麻與自然晨光下的農產品攝影 |
| 工作區 | 頁首固定「生活消費｜社務系統」雙選項切換 |
| 生活消費導覽 | 首頁／團購／便當／訂單／我的；購物車移至頁首 |
| 社務導覽 | 社務首頁／社員／活動／提案／我的 |
| 管理導覽 | 總覽／販售／訂單與物流／社務／設定 |
| 觸控與文字 | 觸控區至少 44px，輔助文字至少 12px |
| 商品圖 | 列表 4:3、詳情 3:2 且限制最大高度 |
| 活動圖 | 16:9 |

## 生成提示詞摘要

設計與缺少的展示素材使用內建圖像生成工具建立，共用以下提示詞基準：

> 高擬真跨平台農產品電商 App「十里方圓」，繁體中文，暖米白紙張質感、深林綠與柿橘，清楚的安全區與底部導覽；使用台灣產地農產品攝影，排除紫藍漸層、玻璃擬態、巢狀卡片與網站式版面。

一般商品、團購與便當一律使用同一個 `CatalogCard`。團購只增加狀態、截止、門檻及細進度條，長描述放到詳情，避免團購圖片佔滿首屏。

商品素材提示詞基準：

> 台灣農產的寫實編輯式商品攝影，單一產品放在竹籃、米色亞麻與深色木桌上，柔和晨光、正方形構圖、無人物、無包裝、無文字、無 Logo、無浮水印。

新上架商品尚無照片時，改用包含多種蔬果、米袋與無標示牛皮紙包裝的中性竹籃攝影，避免錯把小白菜照片套用到其他品項。

## 本次生成素材

### 便當 4:3

> Photorealistic editorial food photography for a Taiwanese cooperative mobile app: a wholesome Taiwanese lunchbox with white rice, seasonal green vegetables, colorful side dishes, tofu, and a soy-glazed main dish, arranged neatly in a reusable rectangular bento box. Warm natural wood, beige linen, a subtle bamboo basket edge, soft morning daylight, trustworthy local-farm atmosphere, appetizing but realistic, clean 4:3 composition with breathing room, no people, no hands, no text, no logo, no watermark, no brand packaging.

檔案：`assets/meals/taiwanese-lunchbox.png`

### 社員健行 16:9

> Photorealistic documentary-style editorial photography for a Taiwanese cooperative community app: five Taiwanese adults of varied ages hiking together on a lush subtropical forest trail, seen naturally from a slight distance, friendly conversation and mutual support, practical daypacks and outdoor clothing in forest green, beige, rust orange and navy. Soft overcast daylight through leaves, warm trustworthy community feeling, realistic candid composition, wide 16:9 frame with breathing room for app crop, no staged poses, no text, no logos, no watermark.

檔案：`assets/community/member-hike.png`
