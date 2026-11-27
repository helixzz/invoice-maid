# Invoice Maid（发票女仆）品牌 VI 设计规范与 UI 架构重设计方案

> **文档版本**：v1.0（适配 2026 全新女仆形象 `assets/images/invoice-maid-2026.png`）
> **适用范围**：Invoice Maid Web 前端（Vue 3 + Tailwind CSS 3.4）、产品文案与品牌延展
> **核心定位**：**日系奶油手账 × 现代财务桌面（Warm Stationery Ledger）**——把繁琐的票据整理，变成一张温润、可靠、让人松一口气的书桌。

---

## 目录

1. [零、现有 WebUI 实测痛点审计](#零现有-webui-实测痛点审计)
2. [一、角色定位与命名提案](#一角色定位与命名提案)
3. [二、品牌视觉定位（Aesthetic Direction）](#二品牌视觉定位aesthetic-direction)
4. [三、完整 VI Design Tokens（可直接落地）](#三完整-vi-design-tokens可直接落地)
5. [四、信息架构（IA）与核心页面重设计蓝图](#四信息架构ia与核心页面重设计蓝图)
6. [五、女仆微文案与交互语调手册（Voice & Tone）](#五女仆微文案与交互语调手册voice--tone)
7. [六、前端分阶段落地路线图与测试兼容守则](#六前端分阶段落地路线图与测试兼容守则)

---

## 零、现有 WebUI 实测痛点审计

在对 `frontend/src/router/index.ts`、`AppLayout.vue`、`MaidChatDrawer.vue`、`types/index.ts`、`tailwind.config.js` 以及全部 8 个视图文件进行源码级审计后，归纳出当前 WebUI 的 **6 大核心痛点**：

| 维度 | 实测现状（源码证据） | 体验与品牌代价 |
| :--- | :--- | :--- |
| **1. 品牌与视觉割裂** | `tailwind.config.js` 的 `theme.extend` 为空；全站采用默认 `bg-slate-50` + `bg-blue-600` 冷科技蓝；`MaidChatDrawer.vue` 头部更是深黑 `bg-slate-900`。 | 与 2026 新女仆头像（浅薄荷绿 + 奶油暖纸白 + 栗棕线条 + 小票发饰）彻底脱节，沦为千篇一律的冷感 SaaS 后台模板。 |
| **2. 中英文混杂、语调分裂** | `AppLayout.vue` 导航混排 `Invoices / Upload / 发票女仆 / Settings`；`InvoiceListView.vue` 表头混排 `Invoice No / Buyer / 报销状态 / Type`，按钮并列 `导出 Excel` 与 `Export CSV`；`SettingsView.vue` Tab 混排 `Email Accounts / Scan Operations / AI 模型 / 发票抬头`。 | 严重破坏产品整体感与专业信任度，像不同时期拼凑的半成品。 |
| **3. 全局框架简陋且移动端断链** | `AppLayout.vue` 所有导航项包裹在 `hidden sm:flex` 中，**移动端既无抽屉菜单也无底部导航栏**；顶部也缺乏全局扫描状态与待处理任务提醒。 | 手机端用户打开后无法从列表切换到「发票女仆」去拍小票，也无法进入上传与设置。 |
| **4. 发票台账信息过载与排版缺陷** | `InvoiceListView.vue` 顶部连续堆叠 KPI 网格 + 5 列分类统计 + 按月多币种透视表，把核心表格挤出首屏；多币种 `v-for` 硬塞进 `lg:grid-cols-4` 易错行；表格高达 11 列且**金额列左对齐、未使用等宽数字**；Saved Views 藏在原生 `<select>` 下拉框中。 | 财务核账效率低：眼睛无法快速纵扫对齐金额；每次进页面都要滚半天才能看到票据。 |
| **5. 详情核对与工作台流程割裂** | `InvoiceDetailView.vue` 采用上下堆叠，**原始 PDF 预览被压在最底部**，核对字段与票面必须反复上下滚动；`MaidWorkspaceView.vue` 标题出现硬核术语 `Agent Loop v2.0`，且「预存抬头」藏在 `/settings` 的第 6 个 Tab 里，用户需要跳出工作台维护抬头；上传电子票（`/upload`）与拍码开票（`/maid`）入口毫无引导衔接。 | 核心高频链路（核对票面、拍照开票、维护抬头、补录验证码）充满摩擦感。 |
| **6. 设置页拥挤、高低频混杂** | `SettingsView.vue` 将 6 个横向 Tab（邮箱、扫描、AI 模型、分类规则、IM 通知、发票抬头）平铺在同一窄行，且把日常业务配置与底层 LLM/分类器调优混在一起。 | 窄屏换行溢出，普通用户面对工程配置产生心理压迫感。 |

---

## 一、角色定位与命名提案

### 角色人格内核
- **性格底座**：细心、可靠、记忆力极好，能把散落在各个邮箱、微信截图、纸质小票里的票据归整得井井有条。
- **情绪价值**：礼貌亲切、温柔鼓励，像办公室里那位总是抢先一步替你收好单据、轻声说「别担心，我已经帮您整理好啦」的贴心助手。
- **反差萌点**：偶尔因为太过认真（例如反复核对小数点、害怕漏掉隐藏在垃圾箱里的数电票）而露出一点点可爱的小迷糊，让工具拥有真实的陪伴感，但绝不耽误正事。
- **视觉识别特征**（源自实测头像 `assets/images/invoice-maid-2026.png`）：
  - 柔和栗棕色短发 + 琥珀焦糖渐变圆润眼眸 + 浅蜜桃腮红
  - 奶油白荷叶边女仆头饰与衣领（配一枚暖琥珀小纽扣）
  - 复古鼠尾草薄荷绿双丝带与领结 + 浅薄荷圆徽章底纹
  - **标志性品牌图腾**：发间佩戴一枚**金色金属扣 × 锯齿撕边小票造型发夹**

### 角色命名提案（4 个候选）

| 方案 | 中文名 | 英文名 / 昵称 | 灵感来源 | 标志性问候语 | 推荐指数 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **方案 A（首推）** | **米尔**（票据女仆·米尔） | **Miri** / 小米尔 | 融合 **Mi**nt（薄荷）+ C**re**am（奶油）+ Memo**ri**es（长期记忆）+ 日系软萌发音「ミリ」，中文又暗合「**细密梳理**」之意，好念好记、兼具温度与品牌延展性。 | 「您辛苦啦，我是票据女仆米尔～别担心，今天新到的发票我都按币种和日期夹进账本里啦！」 | ★★★★★ **首推** |
| **方案 B** | **小薄荷** | **Minty** / 薄荷酱 | 直取头像最具记忆点的浅薄荷绿徽章与薄荷领结，零认知门槛，清新治愈，像办公桌上那盒随时让人松口气的薄荷糖。 | 「叮咚～小薄荷报到！刚帮您巡检完邮箱，3 张新发票已经稳稳收好，只差您盖个已报销章咯。」 | ★★★★☆ |
| **方案 C** | **栗子**（栗小簿） | **Marron** / 小栗 | 呼应柔软的栗棕色短发、焦糖瞳色与「账簿」意象；栗子暖润踏实，自带日系奶油手账与咖啡馆书桌的安心感。 | 「先喝口热茶歇会儿吧～栗子把这周零散的小票都登记齐了，咦……这张外币汇率我再帮您复核一次哦！」 | ★★★★☆ |
| **方案 D** | **小票酱** | **Pipi** / 皮皮 | 取自她发间独一无二的锯齿小票发夹（Paper / Receipt Clip），俏皮活泼，凸显「票据专家 + 偶尔小迷糊」的二次元轻喜剧魅力。 | 「呼——差一点就漏掉藏在订阅邮件里的那张数电票啦！还好小票酱眼尖，已经妥妥放进本月报销夹咯～」 | ★★★☆☆ |

> **首推说明**：建议正式品牌对外采用 **「Invoice Maid · 票据女仆米尔（Miri）」**（UI 紧凑处简称 **「米尔」** 或 **「女仆米尔」**）。「米尔」避免了直白叫「小薄荷/小票酱」在严肃财务报表导出、邮件通知、企业多用户场景下过于幼态的风险，同时在对话抽屉、空状态与提示语里又极其软糯亲切。下文示例均以首推名 **「米尔」** 书写（若最终选定其他候选名，全局替换称谓即可）。

---

## 二、品牌视觉定位（Aesthetic Direction）

### 核心美学概念：**日系奶油手账 × 现代财务桌面（Warm Stationery Ledger）**

我们彻底告别泛滥的「冷灰底 + 科技蓝 + 紫蓝渐变 AI 模板」，而是将 Invoice Maid 塑造成**一张在晨光中井然有序的日系手账财务桌面**：
- **底色是温润的奶油手工纸**（而非刺眼纯白 `#FFFFFF` 与冰冷灰 `#F8FAFC`），长时间对账不伤眼；
- **墨水是温和的深栗棕**（直接取自头像线稿 `#482418` 系），比纯黑柔和，却拥有 AAA 级阅读锐度；
- **主色是沉静可靠的鼠尾草薄荷绿**（由头像浅薄荷圆徽 `#CCE4C0` 向下延展深阶 `#3D7358`），既延续二次元女仆装的清爽辨识度，又在财务语境中传递「核对通过、安心无误」的心理暗示；
- **点缀是焦糖琥珀与淡蜜桃腮红**（取自头像衣领纽扣与双颊红晕），用来承托提醒、徽章、女仆情感反馈。

### 为什么「治愈系手账」与「严肃财务工具」毫不冲突？
1. **高对比墨水 × 低疲劳纸底**：财务对账最怕高饱和刺眼色块和视觉噪音。奶油暖纸底搭配深栗墨水字，既消除冰冷焦虑感，又让数字与关键信息自然跳出。
2. **纸质账本的天然秩序感**：传统优秀手账/会计凭证本就依靠**等宽数字右对齐、细虚线分区、分类索引纸签、清晰状态印章**来承载极高的信息密度——这与现代 Web 财务台账的需求 100% 同构。
3. **情感层与数据层严格分层**：数据表格、发票详情、筛选控件保持克制利落的现代工具效率；插画、女仆语录、锯齿票边与盖章反馈只在「入口、空状态、完成态、陪伴抽屉」四个节点精准出现，**治愈不幼稚，专业不冷漠**。

### 四大标志性视觉语法（克制运用原则）

| 视觉语法 | 灵感来源 | 推荐应用场景（克制白名单） | 禁止滥用红线 |
| :--- | :--- | :--- | :--- |
| **① 锯齿撕票边（Perforated Edge）** | 女仆发间的小票发夹 | 登录欢迎卡底边、详情页票据摘要头、拍码开票成功回执 | 禁止用在普通列表行、表单输入框、弹窗全边框 |
| **② 票据虚线折痕（Ledger Dashed Line）** | 报销单撕裂线与账本装订线 | 账本汇总卡片内部分隔、详情页字段分组、女仆任务时间轴 | 表格数据行仍用极细实线，保证快速扫读流畅度 |
| **③ 印章式报销戳（Stamp Badge）** | 财务核销红/绿印章 | 报销状态（`已报销` / `报销中` / `未报销` / `无需报销`），详情页盖章态微倾 `-1.5deg` | 仅限状态类标签，普通操作按钮不使用印章样式 |
| **④ 暖纸点阵与栗棕柔影（Dot-Grid & Warm Shadow）** | 5mm 点阵手账内页 | 登录页背景、空状态画布、上传拖放激活区；全站阴影使用 `rgba(72,36,24,α)` | 禁止在数据密集表格底层铺点阵，避免干扰文字 |

---

## 三、完整 VI Design Tokens（可直接落地）

### 3.1 色彩系统与 WCAG AA 对比度校验

#### A. 背景与表面分层（Cream Paper Surfaces）
从头像真实奶油白（`#FCF0E4` / `#FCF0D8` / `#F0E4D8`）提炼并拉出清晰层次，保证卡片与画布边界分明、无发脏感：

| Token | Hex | 用途说明 |
| :--- | :--- | :--- |
| `--bg-canvas` | `#FAF3EA` | 全局页面底色（温润奶油手账纸，替代 `bg-slate-50`） |
| `--bg-paper` | `#FFFBF5` | 主内容卡片/表格底色（高明度暖白纸，替代 `bg-white`） |
| `--bg-elevated` | `#FFFFFF` | 浮层弹窗、下拉菜单、悬浮抽屉（最高层级纯洁亮面） |
| `--bg-subtle` | `#F4E9DC` | 侧边栏次底、表头底色、只读字段框、分组容器 |
| `--bg-stripe` | `#FCF7F0` | 账本表格斑马纹 / 鼠标悬停行微亮底色 |
| `--border-hairline` | `#EAE0D3` | 表格行分割、卡片微边框 |
| `--border-default` | `#DDD0C0` | 输入框默认边框、次级按钮描边 |
| `--border-strong` | `#C7B4A0` | 悬停边框、票据虚线折痕 |

#### B. 品牌主色：鼠尾草薄荷阶梯（Sage Mint）
保留头像原色 `#CCE4C0` / `#D8F0CC` 作为品牌光晕与选中淡底，并严格构建深阶保证白字按钮对比度 **≥ 4.5:1**：

| 色阶 Token | Hex | 语义角色 | 对比度校验（WCAG 2.1） |
| :--- | :--- | :--- | :--- |
| `mint-50` | `#F2F8EE` | 极浅薄荷底、侧栏选中态底色 | 配 `ink-900` (`#482418`) = **12.8:1 (AAA)** |
| `mint-100` | `#E1F0D9` | 浅薄荷徽章底（贴近头像 `#D8F0CC`） | 配 `mint-800` (`#244937`) = **8.9:1 (AAA)** |
| `mint-200` | `#CCE4C0` | **头像原吸取薄荷圆徽色**、图标光环、聚焦柔晕 | 配 `ink-900` (`#482418`) = **10.6:1 (AAA)** |
| `mint-300` | `#A5CCA0` | 装饰描边、次级标签边框 | 仅作装饰边框，不放浅字 |
| `mint-400` | `#74A882` | 头像领结丝带色、图标高亮、进度条轨道填充 | 非文本图形对比度 ≥ 3:1 |
| `mint-500` | `#4E876A` | 强调图标、聚焦框线、活跃指示灯 | 对白 4.2:1（用于图标/边框，不放小号白字） |
| **`mint-600`** | **`#3D7358`** | **主操作按钮（Primary CTA）、选中胶囊底色** | 对 `#FFFFFF` = **5.54:1 (AA+)** · 对 `#FFFBF5` = **5.40:1 (AA+)** |
| **`mint-700`** | **`#2F5E47`** | **主按钮 Hover 态、深绿文字链接** | 对 `#FFFFFF` = **7.47:1 (AAA)** · 对 `#FAF3EA` = **6.78:1 (AA+)** |
| `mint-800` | `#244937` | 浅薄荷底色上的深色文字与印章字 | 对 `mint-100` (`#E1F0D9`) = **8.9:1 (AAA)** |
| `mint-900` | `#193528` | 极深墨绿（深色局部强调，如女仆头部徽章） | 对 `#FFFBF5` = **12.9:1 (AAA)** |

#### C. 栗棕墨水文字阶梯（Chestnut Ink）
取自头像线稿 `#482418` / `#543024` / `#603C30` 与暖栗辅色 `#845448` / `#A86C54`：

| Token | Hex | 用途 | 在 `#FFFBF5` 上对比度 | 在 `#FAF3EA` 上对比度 |
| :--- | :--- | :--- | :--- | :--- |
| `--ink-primary` (`ink-900`) | `#482418` | 页面主标题、金额数字、发票号、核心正文 | **13.5:1 (AAA)** | **12.6:1 (AAA)** |
| `--ink-secondary` (`ink-700`) | `#634235` | 次级正文、销方/购方名称、表单标签 | **8.62:1 (AAA)** | **8.08:1 (AAA)** |
| `--ink-muted` (`ink-500`) | `#7E5E4F` | 表头文字、时间戳、辅助说明、次要图标 | **5.66:1 (AA+)** | **5.31:1 (AA+)** |
| `--ink-placeholder` (`ink-400`) | `#A68B7C` | 输入框占位符、空值 `—`、禁用态图标 | 仅限占位/禁用 | 仅限占位/禁用 |

#### D. 暖栗 / 焦糖 / 蜜桃情感辅色（Caramel & Peach Accents）

| Token | Hex | 灵感来源与用途 |
| :--- | :--- | :--- |
| `--caramel-500` | `#A86C54` | 头像发饰金属光泽 & 暖栗装饰图标、二级强调线 |
| `--caramel-600` | `#845448` | 暖栗辅色文字、手账书签图标、管理员徽章字 |
| `--honey-500` | `#C78A3B` | 头像领口小纽扣金、星标/默认抬头标记 |
| `--peach-100` | `#FCE4D8` | 头像腮红淡蜜桃底——用于女仆欢迎卡光晕、温馨提示底 |
| `--peach-200` | `#F6CBB9` | 腮红装饰边框、新消息轻微脉冲高亮 |

#### E. 语义状态色（全面替换刺眼 Tailwind 原色）

##### 1. 报销状态印章色（Reimbursement Stamp Palette）
| 状态 | 中文标签 | 底色 | 边框 | 文字色（对比度 ≥ 5.5:1） | 视觉隐喻 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `reimbursed` | 已报销 | `#E8F5E3` | `#9CC49A` | `#265338` | 抹茶核销绿印（安心完结） |
| `pending` | 报销中 | `#FDF2DF` | `#DFB97A` | `#7D4E15` | 焦糖蜂蜜印（流转进行中） |
| `unreimbursed` | 未报销 | `#FCEAE6` | `#E4A699` | `#8F3528` | 蜜桃朱砂印（待处理醒目但不刺眼） |
| `not_applicable` | 无需报销 | `#F2EBE2` | `#D3C5B6` | `#6A574B` | 燕麦暖灰印（静默归档） |

##### 2. 五类发票分类纸签色（Invoice Category Paper Tabs）
| 枚举值 | 中文标签 | 底色 | 边框 | 文字色 | 左侧色条/圆点 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `vat_invoice` | 增值税发票 | `#E7F3EC` | `#A8CEB9` | `#245540` | `#3D7358` |
| `overseas_invoice` | 境外票据 | `#F1ECF7` | `#C8B9DC` | `#503A6E` | `#7B60A3` |
| `receipt` | 收据 | `#FBF0E1` | `#E0BF95` | `#7A4C1F` | `#B9782C` |
| `proforma` | 形式发票 | `#EBF2F5` | `#B4CBD6` | `#2F5566` | `#4E7F94` |
| `other` | 其它 | `#F3ECE4` | `#D6C8B9` | `#634F43` | `#8A7364` |

##### 3. AI 置信度 & 危险操作 & 开票追踪状态色
- **AI 置信度**：
  - 高置信（`> 0.80`）：文字 `#265338` / 进度条 `#3D7358`
  - 中置信（`0.50 – 0.80`，建议核对）：文字 `#8A5616` / 进度条 `#C78A3B`
  - 低置信（`≤ 0.50`，重点校对）：文字 `#96362A` / 进度条 `#B54638`
- **危险 / 删除操作（暖朱砂红）**：
  - 按钮填充 `#B54638`（对纯白 **5.38:1 AA+**）、Hover `#96362A`、浅警示底 `#FCEAE6`、警示框边 `#E4A699`
- **拍码开票任务状态（`MaidWorkspaceView`）**：
  - `awaiting_profile` 待选抬头：`#FDF2DF` / `#DFB97A` / `#7D4E15`
  - `queued` 排队等待：`#F2EBE2` / `#D3C5B6` / `#6A574B`
  - `filling` 自动填表中：`#EBF2F5` / `#B4CBD6` / `#2F5566`（配柔和呼吸点）
  - `needs_attention` 需您协助：`#FCEAE6` / `#E4A699` / `#8F3528`（高亮展开引导卡）
  - `submitted` 等待收票：`#F1ECF7` / `#C8B9DC` / `#503A6E`
  - `completed` 已收票归档：`#E8F5E3` / `#9CC49A` / `#265338`
  - `cancelled` 已取消：`#F3ECE4` / `#DDD0C0` / `#8A7364`

---

### 3.2 字体排印体系（Typography）

彻底拒绝千篇一律的 `Inter / Roboto / Arial`，采用**「温柔衬线展示 + 清晰人文无衬线正文 + 金融等宽数字」**三轨搭配，并保证国内网络零阻塞、本地字体优雅回退：

#### 三轨字族定义
| 轨道 | CSS 变量 | 推荐字族栈（优先自托管 WOFF2，其次系统高质量字体） | 适用场景 |
| :--- | :--- | :--- | :--- |
| **展示 / 手账标题** | `--font-display` | `'Fraunces', 'LXGW WenKai Lite', 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif` | 品牌 Logo 旁标语、登录欢迎大标题、空状态治愈标题、手账账本封面标题 |
| **UI 界面与正文** | `--font-sans` | `'Plus Jakarta Sans', 'HarmonyOS Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif` | 导航、按钮、表头、表单标签、对话气泡、长摘要文本 |
| **票据数字与编码** | `--font-mono` | `'JetBrains Mono', 'IBM Plex Mono', 'SFMono-Regular', 'Cascadia Mono', Consolas, monospace` | **金额、发票号码、税号、日期、置信度百分比、币种代码、解析方式标签** |

> **国内网络加载策略**：不直连 `fonts.googleapis.com`。可在 `frontend/public/fonts/` 仅自托管极轻量子集（`Plus Jakarta Sans` Latin 约 25KB、`JetBrains Mono` 数字与拉丁子集约 18KB、`Fraunces` 拉丁子集约 22KB），中文字体优先使用系统已安装的 `PingFang SC / HarmonyOS Sans SC / Songti SC`，可选异步按需加载霞鹜文楷子集（`font-display: swap`），确保首屏渲染毫秒级不闪烁。

#### 字号与行高标尺
| 层级 | 字号 / 行高 | 字重 | 字族与特性 | 典型场景 |
| :--- | :--- | :--- | :--- | :--- |
| `Display` | `28px / 1.25` | `600` | `--font-display`, `-0.015em` | 登录欢迎语、空状态大标题 |
| `Page Title` | `22px / 1.3` | `700` | `--font-sans`, `-0.01em` | 页面主标题（如「票据台账」「米尔工作台」） |
| `KPI Amount` | `26px / 1.15` | `700` | `--font-mono`, `tabular-nums` | 账本多币种汇总大数字、详情页主金额 |
| `Section Title` | `16px / 1.4` | `600` | `--font-sans` | 卡片区块标题、抽屉标题 |
| `Body` | `14px / 1.6` | `400` | `--font-sans` | 表单内容、列表文字、对话消息 |
| `Table Cell` | `13px / 1.45` | `400/500` | `--font-sans`（数字列 `--font-mono tabular-nums`） | 发票台账高密度数据行 |
| `Table Header` | `12px / 1.3` | `600` | `--font-sans`, `0.04em` | 表头、字段 label |
| `Badge / Stamp` | `11.5px / 1.2` | `600` | `--font-sans`, `0.03em` | 分类纸签、报销印章、工具调用徽章 |

#### 金融排版铁律（Mandatory）
1. **等宽数字强制开启**：所有金额、计数、日期、发票号、税号、页码必须加 `tabular-nums`（`font-variant-numeric: tabular-nums lining-nums`）。
2. **金额严格右对齐**：表格中的金额列与月度透视表列，表头与单元格**全部右对齐**（`text-right`），币种符号与数值留出 `2px` 呼吸间距（如 `¥ 1,280.00` 或 `$ 49.90`，加千分位逗号提升扫读速度！）。
3. **发票号截断可复制**：20 位中国数电票号在表格中以等宽显示，支持悬停显示全号与一键复制微按钮，绝不撑爆列宽。

---

### 3.3 圆角、暖调阴影与表面质感规范

- **圆角体系（温润不幼稚）**：
  - `rounded-card`: `16px`（主账本卡片、欢迎卡）
  - `rounded-panel`: `12px`（嵌套区块、筛选盒、预览容器）
  - `rounded-control`: `10px`（输入框、按钮、下拉菜单）
  - `rounded-stamp`: `6px`（印章标签，区别于 `rounded-full` 的普通胶囊，形成独特辨识度）
  - `rounded-pill`: `9999px`（筛选 Chips、女仆悬浮气泡）
- **栗棕暖调阴影（拒绝冷黑阴影）**：
  - `--shadow-card`: `0 1px 2px rgba(72, 36, 24, 0.04), 0 4px 12px -2px rgba(72, 36, 24, 0.05)`
  - `--shadow-float`: `0 8px 24px -4px rgba(72, 36, 24, 0.10), 0 2px 6px rgba(72, 36, 24, 0.04)`（用于女仆对话抽屉、底部批量 Dock、弹窗）
  - `--shadow-mint-glow`: `0 6px 18px -4px rgba(61, 115, 88, 0.28)`（用于主操作按钮悬浮反馈与右下角女仆呼出键）

---

### 3.4 动效与微交互规范（Motion）

原则：**像翻动一页细腻的手账纸、轻轻盖下一枚印章——克制、顺滑、从不喧宾夺主**。
- **缓动曲线**：
  - 纸张展开 / 抽屉弹入：`cubic-bezier(0.22, 1, 0.36, 1)`（轻快起势，温柔减速停稳）
  - 印章轻落反馈：`cubic-bezier(0.34, 1.56, 0.64, 1)`（极轻微的回弹，时长仅 `220ms`）
  - 常规 Hover 过渡：`150ms ease-out`
- **四大高光微交互**：
  1. **页面首屏阶梯入场**：账本总览卡 → 筛选工具条 → 台账表格按 `0ms / 50ms / 100ms` 轻幅上浮淡入（`translateY(6px) → 0`），避免生硬闪现。
  2. **报销盖章动画**：当用户将发票标记为「已报销」时，状态印章触发一次 `scale(1.14) rotate(-4deg) → scale(1) rotate(-1.5deg)` 的清脆落印动效。
  3. **女仆陪伴抽屉展开**：从右下角悬浮头像处按 `transform-origin: bottom right` 弹性展开，新消息轻柔滑入；思考中显示三颗薄荷色小圆点波浪律动（替代生硬文字 `animate-pulse`）。
  4. **无障碍兜底**：全局支持 `@media (prefers-reduced-motion: reduce)`，关闭非必要位移动画。

---

### 3.5 可直接落地的代码配置

#### 1) `frontend/src/style.css`（CSS Variables + 票据手账工具类）

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  /* Surfaces — Cream Stationery Paper */
  --bg-canvas: #FAF3EA;
  --bg-paper: #FFFBF5;
  --bg-elevated: #FFFFFF;
  --bg-subtle: #F4E9DC;
  --bg-stripe: #FCF7F0;

  /* Borders */
  --border-hairline: #EAE0D3;
  --border-default: #DDD0C0;
  --border-strong: #C7B4A0;

  /* Brand — Sage Mint */
  --mint-50: #F2F8EE;
  --mint-100: #E1F0D9;
  --mint-200: #CCE4C0;
  --mint-300: #A5CCA0;
  --mint-400: #74A882;
  --mint-500: #4E876A;
  --mint-600: #3D7358;
  --mint-700: #2F5E47;
  --mint-800: #244937;
  --mint-900: #193528;

  /* Ink — Chestnut Espresso */
  --ink-primary: #482418;
  --ink-secondary: #634235;
  --ink-muted: #7E5E4F;
  --ink-placeholder: #A68B7C;

  /* Warm Accents */
  --caramel-500: #A86C54;
  --caramel-600: #845448;
  --honey-500: #C78A3B;
  --peach-100: #FCE4D8;
  --peach-200: #F6CBB9;
  --danger-600: #B54638;
  --danger-700: #96362A;

  /* Typography */
  --font-display: 'Fraunces', 'LXGW WenKai Lite', 'Source Han Serif SC', 'Songti SC', Georgia, serif;
  --font-sans: 'Plus Jakarta Sans', 'HarmonyOS Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  --font-mono: 'JetBrains Mono', 'IBM Plex Mono', 'SFMono-Regular', 'Cascadia Mono', Consolas, monospace;

  /* Shadows */
  --shadow-card: 0 1px 2px rgba(72, 36, 24, 0.04), 0 4px 12px -2px rgba(72, 36, 24, 0.05);
  --shadow-float: 0 10px 28px -6px rgba(72, 36, 24, 0.12), 0 2px 6px rgba(72, 36, 24, 0.04);
  --shadow-mint: 0 6px 18px -4px rgba(61, 115, 88, 0.28);
}

@layer base {
  html {
    background-color: var(--bg-canvas);
    color: var(--ink-primary);
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }

  body {
    background-color: var(--bg-canvas);
    color: var(--ink-primary);
  }
}

@layer utilities {
  /* 金融数字等宽对齐 */
  .font-ledger {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums lining-nums;
  }

  /* 手账极淡点阵背景（仅用于登录页/空状态/上传激活区） */
  .bg-stationery-dots {
    background-color: var(--bg-canvas);
    background-image: radial-gradient(rgba(132, 84, 72, 0.15) 1px, transparent 1px);
    background-size: 20px 20px;
  }

  /* 票据细虚线分隔 */
  .border-ledger-dashed {
    border-style: dashed;
    border-color: var(--border-strong);
  }

  /* 克制的锯齿撕票底边（用于欢迎卡、详情摘要头、开票成功回执） */
  .receipt-edge-bottom {
    position: relative;
  }
  .receipt-edge-bottom::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: -6px;
    height: 6px;
    background-image: radial-gradient(circle at 6px 6px, transparent 4px, var(--bg-paper) 4.5px);
    background-size: 12px 6px;
    background-repeat: repeat-x;
    pointer-events: none;
  }

  /* 印章式报销戳微倾效果 */
  .stamp-badge {
    border-radius: 6px;
    letter-spacing: 0.03em;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.55);
  }
  .stamp-tilted {
    transform: rotate(-1.5deg);
  }
}

@keyframes stamp-drop {
  0% { transform: scale(1.16) rotate(-4deg); opacity: 0.7; }
  70% { transform: scale(0.97) rotate(-1deg); opacity: 1; }
  100% { transform: scale(1) rotate(-1.5deg); opacity: 1; }
}

.animate-stamp-drop {
  animation: stamp-drop 220ms cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

@media (prefers-reduced-motion: reduce) {
  .animate-stamp-drop,
  .transition-all,
  .transition-colors {
    animation: none !important;
    transition-duration: 0.01ms !important;
  }
}
```

#### 2) `frontend/tailwind.config.js`（直接可用的主题扩展）

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cream: {
          canvas: '#FAF3EA',
          paper: '#FFFBF5',
          subtle: '#F4E9DC',
          stripe: '#FCF7F0',
          border: '#EAE0D3',
          line: '#DDD0C0',
          strong: '#C7B4A0',
        },
        mint: {
          50: '#F2F8EE',
          100: '#E1F0D9',
          200: '#CCE4C0',
          300: '#A5CCA0',
          400: '#74A882',
          500: '#4E876A',
          600: '#3D7358',
          700: '#2F5E47',
          800: '#244937',
          900: '#193528',
        },
        ink: {
          900: '#482418',
          700: '#634235',
          500: '#7E5E4F',
          400: '#A68B7C',
        },
        caramel: {
          500: '#A86C54',
          600: '#845448',
        },
        honey: {
          50: '#FDF2DF',
          200: '#DFB97A',
          500: '#C78A3B',
          700: '#7D4E15',
        },
        peach: {
          50: '#FFF4EE',
          100: '#FCE4D8',
          200: '#F6CBB9',
        },
        cinnabar: {
          50: '#FCEAE6',
          200: '#E4A699',
          600: '#B54638',
          700: '#96362A',
          800: '#8F3528',
        },
      },
      fontFamily: {
        display: ['var(--font-display)'],
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        card: '16px',
        panel: '12px',
        control: '10px',
        stamp: '6px',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        float: 'var(--shadow-float)',
        mint: 'var(--shadow-mint)',
      },
    },
  },
  plugins: [],
}
```

#### 3) `types/index.ts` 推荐升级的语义色映射（落地参考）

```ts
export const CATEGORY_COLORS: Record<InvoiceCategory, string> = {
  vat_invoice: 'bg-[#E7F3EC] text-[#245540] border-[#A8CEB9]',
  overseas_invoice: 'bg-[#F1ECF7] text-[#503A6E] border-[#C8B9DC]',
  receipt: 'bg-[#FBF0E1] text-[#7A4C1F] border-[#E0BF95]',
  proforma: 'bg-[#EBF2F5] text-[#2F5566] border-[#B4CBD6]',
  other: 'bg-[#F3ECE4] text-[#634F43] border-[#D6C8B9]',
}

export const REIMBURSEMENT_COLORS: Record<ReimbursementStatus, string> = {
  unreimbursed: 'bg-[#FCEAE6] text-[#8F3528] border-[#E4A699]',
  pending: 'bg-[#FDF2DF] text-[#7D4E15] border-[#DFB97A]',
  reimbursed: 'bg-[#E8F5E3] text-[#265338] border-[#9CC49A]',
  not_applicable: 'bg-[#F2EBE2] text-[#6A574B] border-[#D3C5B6]',
}
```

---

## 四、信息架构（IA）与核心页面重设计蓝图

### 4.1 全站统一中文术语规范（彻底消除中英混杂）

| 现有混杂文案 | 统一后中文规范 | 备注 |
| :--- | :--- | :--- |
| `Invoices` | **票据台账** | 主视图，凸显账本概念 |
| `Upload` | **上传票据** | 明确为手动归档电子票 |
| `发票女仆 / Agent Loop v2.0` | **米尔工作台**（或「女仆工作台」） | 去掉内部工程代号 `Agent Loop v2.0` |
| `Settings` | **偏好设置** | 柔和自然 |
| `Admin` | **用户管理** | 仅管理员可见 |
| `All Invoices` / `Save View` | **全部票据** / **保存筛选视图** | |
| `Export CSV` / `导出 Excel` | 合并为一个「**导出账单**」下拉菜单（Excel 报销表 / CSV 明细） | 减少按钮噪音 |
| `Email Accounts` / `Scan Operations` | **邮箱与票源** / **扫描与日志** | |
| `Invoice No / Buyer / Seller / Amount / Date / Summary` | **发票号码 / 购买方 / 销售方 / 金额 / 开票日期 / 项目摘要** | 全表统一中文 |

---

### 4.2 新全局框架（App Shell Architecture）

告别简陋的单层冷白顶栏，升级为**「温暖手账左侧纤细侧栏 + 轻量顶部状态栏 + 移动端底部栏 + 右下角米尔陪伴抽屉」**：

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ [桌面端 ≥ 1024px]                                                        │
│ ┌─────────────────┬────────────────────────────────────────────────────┐ │
│ │ 🌿 侧栏 236px   │  顶部轻状态栏 56px（bg-cream-paper/80 backdrop）    │ │
│ │ (bg-cream-subtle│  [当前页标题]      [扫描心跳状态胶囊] [快捷上传+]  │ │
│ │  border-r)      ├────────────────────────────────────────────────────┤ │
│ │                 │                                                    │ │
│ │  (女仆圆形头像)  │              主工作画布（bg-cream-canvas）          │ │
│ │  Invoice Maid   │              max-w-[1360px] mx-auto px-6 py-6      │ │
│ │  票据女仆·米尔   │                                                    │ │
│ │                 │                                                    │ │
│ │  ─── 账本工作 ── │                                                    │ │
│ │  ● 票据台账     │                                                    │ │
│ │  ○ 上传票据     │                                                    │ │
│ │  ○ 米尔工作台 [2]│  ← 红/蜜桃角标提示 needs_attention 任务数          │ │
│ │                 │                                                    │ │
│ │  ─── 规则配置 ── │                                                    │ │
│ │  ○ 偏好设置     │                                                    │ │
│ │  ○ 用户管理*    │                                                    │ │
│ │                 │                                                    │ │
│ │  ─── 底部账号 ── │                                      ┌──────────┐ │ │
│ │  [头像] 账号邮箱 │                                      │ (米尔)   │ │ │
│ │  改密 · 退出    │                                      │ 呼出米尔 │ │ │
│ └─────────────────┴──────────────────────────────────────┴──────────┘─┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

- **桌面端左侧导航栏（`236px`，中屏 `768px–1023px` 自动折为 `72px` 图标轨）**：
  - 顶部放置 `40px` 圆形 `invoice-maid-2026.png` 头像徽章（配浅薄荷光环）+ `Invoice Maid` 展示体品牌字 + 小号副标 `票据女仆 · 米尔`。
  - 导航分组为「**日常票据**（票据台账 / 上传票据 / 米尔工作台）」与「**系统管理**（偏好设置 / 用户管理）」。
  - 选中态为柔和圆角薄荷纸条 `bg-mint-100 text-mint-800` + 左侧 `3px` 鼠尾草薄荷书签竖条。
  - **跨页角标联动**：「米尔工作台」旁展示待处理开票任务（`awaiting_profile` + `needs_attention`）计数角标，彻底解决工作台与列表割裂问题。
- **顶部轻状态栏（`56px`）**：
  - 左侧：面包屑 / 当前页标题。
  - 右侧：**全局邮箱扫描脉冲胶囊**（扫描进行中实时显示进度，平时显示「上次同步：10 分钟前」且点击可直达扫描页）+ 一键「**+ 录入票据**」主按钮（可直达上传或拍小票）。
- **移动端适配（`< 768px`）**：
  - 补齐**底部固定导航栏（`60px` + `env(safe-area-inset-bottom)`）**，4 个核心触控入口：`台账` · `上传` · `工作台` · `设置`，确保手机端拍照开票一键直达。
  - 右下角米尔悬浮呼出按钮在移动端自动抬高至 `bottom-20 right-4`，绝不遮挡底部导航。

---

### 4.3 逐页重设计蓝图

#### 页面 1：登录与注册页（`LoginView.vue` / `RegisterView.vue`）
- **痛点解决**：替换毫无特色的纯白居中框，打造第一眼就难忘的治愈系品牌入口。
- **布局方案（桌面端双栏手账卡 / 移动端上下合拢）**：
  - 全屏铺设极其克制的 `bg-stationery-dots` 奶油手账点阵背景。
  - 中央为一张 `max-w-[880px]` 的双栏精致卡片（`rounded-card shadow-float`）：
    - **左栏（46%，浅薄荷 × 奶油暖光渐变 `#EBF5E5 → #FCF4EA`）**：
      - 居中展示 `140px × 140px` 的高清新女仆形象 `invoice-maid-2026.png`，外圈配奶油白蕾丝感细双环；
      - 展示体标题：`Invoice Maid` + 副标 `票据女仆 · 米尔`；
      - 一句温柔的手账气泡问候：「欢迎回来～邮箱里新到的票据，我已经按日期为您整理好啦。」
      - 底部 3 个精致微标签：`多币种自动归账` · `拍码代填开票` · `长期报销记忆`。
    - **右栏（54%，`bg-cream-paper` + 底部可选 `receipt-edge-bottom` 锯齿撕票边）**：
      - 顶部标题「打开您的票据手账」（注册页为「新建一本票据手账」）；
      - 温润的奶油描边输入框（Focus 时显示 `ring-2 ring-mint-200 border-mint-500`）；
      - 主按钮使用 `bg-mint-600 hover:bg-mint-700 text-white shadow-mint`，文案为「翻开账本」/「正在核对钥匙…」。

#### 页面 2：总览与票据台账（`InvoiceListView.vue`）
- **痛点解决**：首屏被 3 层统计卡片压爆、多币种卡片错行、11 列宽表格左对齐数字混乱、Saved Views 藏在下拉框里。
- **新结构（4 层清晰节奏，首屏即可看见发票表格！）**：
  1. **顶层：精炼「账本总览带」（Compact Ledger Ribbon，高度压缩至约 `116px`）**：
     - 采用 **3 栏自适应弹性卡片**（彻底解决多币种错行问题）：
       - **卡片 1（多币种账本主卡，占 2 份宽）**：像一本精致的活页账本封面，横向/分段展示各币种总额（`¥ CNY 12,480.00` · `$ USD 340.50` 等），每个币种自带票数小注，右上角提供「**展开月度趋势 ▾**」切换按钮——把原先长期霸屏的「Monthly Spend by Currency」大透视表改为**默认折叠的展开抽屉**，需要看月度趋势时一键展开！
       - **卡片 2（本月新入账）**：展示本月新增票数、总票数，并用迷你彩色进度点条一行浓缩「5 类分类分布」（**把原先独占一行的 5 列 `By Category` 卡片融入此处或与筛选条联动**，点击分类即可直接筛选！）。
       - **卡片 3（米尔值守状态）**：展示活跃邮箱数、上次扫描时间 + 快捷入口（`立即扫一扫` / `有 N 条待确认开票`）。
  2. **第二层：顺手的「手账索引条 & 筛选工作栏」**：
     - **顶部标签行（Saved Views 改为手账索引贴纸 Tabs！）**：横向排列 `全部票据`、`[已保存视图 1]`、`[已保存视图 2]`，右侧紧跟 `+ 保存当前视图` 与删除当前视图小图标，再也不用点开原生 `<select>` 盲选！
     - **搜索与导出行**：左侧宽搜索框（支持搜销方/发票号/摘要/备注）+ 日期范围双框 + 右侧统一的「**导出账单 ▾**」按钮（合并原 `导出 Excel` 与 `Export CSV`）与「**+ 上传票据**」。
     - **折叠/紧凑胶囊行**：分类纸签 Chips 与报销印章 Chips 紧凑排布，激活态直接使用对应语义色或 `mint-600`，一眼分辨当前筛选条件。
  3. **第三层：精简高密度「财务台账表格」（11 列瘦身为 8 列，零横向压迫感）**：
     - 保留核心列并优化排版：
       1. `复选框`（44px）
       2. `销售方 & 摘要`（主行粗体显示 **销售方名称**，副行淡栗色截断显示 `项目摘要` + `发票类型微标`，悬停看完整信息——把原 `Seller` / `Type` / `Summary` 三列优雅合并！）
       3. `购买方 / 抬头`（单行截断，区分多公司抬头）
       4. `发票号码`（**等宽 `--font-mono`**，支持悬停一键复制）
       5. `开票日期`（**等宽 `--font-mono`**，`YYYY-MM-DD`）
       6. `分类`（低饱和手账纸签 `CategoryBadge`）
       7. `报销状态`（**印章式 Stamp Badge**，且支持行内快速切换或悬浮查看标签/备注）
       8. `金额`（**严格右对齐 + 等宽 `--font-mono tabular-nums` + 千分位逗号 + 币种弱化前缀**，如 `CNY  ¥ 1,280.00`）
       9. `操作`（精简图标按钮：查看详情 / 下载原件 / 删除，弱化视觉噪音）
  4. **第四层：底部悬浮批量操作 Dock（Floating Action Bar）**：
     - 勾选票据时，**不再在表格上方插入横条造成整页跳动**，而是在视口底部居中柔和浮出一条深茶薄荷色（`bg-mint-900 text-cream-paper`）悬浮工具条：显示「已选 N 张 · 合计分币种金额」，右侧排列 `盖章：已报销` · `标为报销中` · `打包报销 ZIP` · `批量下载` · `删除`。

#### 页面 3：发票详情核对台（`InvoiceDetailView.vue`）
- **痛点解决**：原始 PDF 被流放到页面最底部，核对数据与原票必须疯狂上下滚动。
- **新结构（桌面端 50 / 50 左右双栏核对台，移动端上下切页）**：
  - **顶部通栏工具头**：返回台账按钮 + 等宽发票号码 + 报销印章（**支持顶部一键快捷盖章切换，无需先点「编辑全部」**！）+ 按钮组（`编辑字段` / `下载原件`）。
  - **左栏（50%，`sticky top-20` 固定随滚）：原始票面预览台**：
    - 顶部展示文件格式胶囊（`PDF` / `OFD` / `XML`）与提取方式（`QR` / `XML` / `VLM` / `LLM`）及新窗口全屏查看按钮；
    - 内部用温润纸边包裹 PDF `<iframe>` 预览（高度自适应 `calc(100vh - 180px)`），实现**左手看原票、右手校数据**的真正专业财务核对体验；OFD/XML 不支持内联时展示带女仆小插图的友好下载引导卡。
  - **右栏（50%）：手账电子回执单**：
    - **顶部「票据摘要头」**（应用克制的 `receipt-edge-bottom` 锯齿撕票边）：突出展示大号等宽金额（`¥ 1,280.00 CNY`）、开票日期、**AI 置信度圆弧/进度条**（低置信度 `< 80%` 时显示米尔温馨提示条：「这张票面字迹有点小模糊，建议对照左侧原件核对一下金额和日期哦」）。
    - **核心字段区（虚线分割）**：购买方、销售方、发票类型、项目摘要（只读态干净清爽，编辑态原位丝滑切换为输入框，不跳动布局）。
    - **报销与归档区**：报销状态印章选择器、标签（改为可直观看到的纸签气泡，而非冰冷逗号字符串）、报销备注。
    - **底部折叠区**：人工校对历史时间轴（保留旧值划除 → 新值抹茶绿高亮对比）。

#### 页面 4：上传票据中心（`InvoiceUploadView.vue`）
- **痛点解决**：与 `/maid` 拍小票割裂；全英文机械文案；缺乏情绪反馈。
- **重设计要点**：
  - **顶部双入口分流提示条**：主区专注「**电子发票批量归档（PDF / XML / OFD，单批最多 200 份，20 并发解析）**」，右上角放置一张浅蜜桃薄荷联动小卡：「手里是线下纸质小票/开票二维码？→ **交给米尔拍码自动开票**（直达 `/maid`）」。
  - **手账感拖放画布**：宽大圆角虚线框（`border-2 border-dashed border-cream-strong bg-cream-paper`），拖拽文件悬停时亮起 `bg-mint-50 border-mint-500` + 极淡手账点阵，配温柔中文引导。
  - **队列列表优化**：每条记录右侧清晰呈现状态徽章 + 等宽进度百分比；重复发票（409）用温和琥珀色标为「账本里已经收过啦」并直接提供「查看已有记录」按钮；全部完成后出现带女仆小头像的安心回执横幅。

#### 页面 5：米尔工作台（`MaidWorkspaceView.vue`）
- **痛点解决**：工程味重、抬头管理被迫跳转设置页、三大模块垂直堆叠失焦、验证码求助无法边看截图边填。
- **重设计要点**：
  - **顶部治愈欢迎头**：`48px` 女仆头像 + 标题「**米尔的票据工作台**」+ 副标「把线下小票二维码交给我，我会按您的预存抬头自动填表、定时盯梢收票，并牢记您的每条报销习惯」+ 操作按钮组（`立即巡检收票`、`管理开票抬头 (N)` 改为**侧滑抽屉直接就地维护**，不再强制跳离工作台！）。
  - **二栏/分段清晰结构**：
    - **上方「快速交办区」（双卡并排）**：
      - 左卡（主推）：📷 **拍照 / 上传小票二维码**（支持粘贴剪贴板图片 `Ctrl+V` 提示 + 移动端直接调起相机）；
      - 右卡：🔗 **粘贴开票链接**（直接提交）。
    - **主区切换（Segmented Tabs，减少长滚动）**：
      - **Tab 1：开票追踪队列（默认，带进行中计数）** —— 每条任务升级为「**票据流转进度卡**」，头部展示商户名 + 右对齐等宽金额 + 语义状态徽章；内部附 4 步迷你流水线（`扫码识别 → 填表提交 → 等待发票 → 自动归档`）；
        - 当状态为 `awaiting_profile`：亮起温暖蜂蜜色选择卡，一键点选公司/个人抬头卡片；
        - 当状态为 `needs_attention`：**左侧直接内嵌表单截图缩略图（点击放大），右侧紧贴验证码输入框与「帮米尔继续提交」主按钮**，告别切新标签页来回看图！
      - **Tab 2：米尔的长期记忆本（规则库）** —— 像翻开女仆的随身备忘小本子：上方是一句话自然添加表单，下方用卡片列表呈现「当遇到关键词 `[大药房]` → 自动盖章 `[无需报销]` + 打标 `[个人消费]`」，清晰可读。

#### 页面 6：偏好设置中心（`SettingsView.vue`）
- **痛点解决**：6 个横向 Tab 拥挤换行、高低频配置混杂。
- **重设计要点**：将顶部 6 个横向 Tab 重构为**桌面端左侧垂直二级菜单（`208px`）+ 右侧卡片内容区**（移动端变为可横向滚动的精致胶囊条），按心智模型分为 **2 大分组**：
  - **分组一 · 日常采集与开票**
    1. `邮箱与票源`（IMAP / POP3 / Outlook OAuth / QQ / SaaS 账号卡片化展示，连接状态与上次同步一眼明了）
    2. `扫描与日志`（手动扫描操作卡 + 实时进度条 + 折叠展开的按邮件聚合提取日志）
    3. `预存开票抬头`（名片式公司/个人抬头卡片，默认抬头佩戴蜂蜜金星标，与工作台共享数据）
  - **分组二 · 智能引擎与通知**
    4. `AI 模型与视觉`（主模型 / 降级链 / VLM 视觉模型 / Embedding 配置 + 一键连通性测试）
    5. `邮件分类规则`（可信发件人、扩展关键词、屏蔽发件人）
    6. `Telegram 通知与助手`（系统告警 Bot + 独立对话 Agent Bot，冲突警示用暖朱砂柔和呈现）

#### 页面 7：管理员用户台（`AdminView.vue`）与女仆陪伴抽屉（`MaidChatDrawer.vue`）
- **`AdminView.vue`**：顶部补齐 3 张轻统计胶囊（总用户数 / 活跃用户 / 累计归档票据），表格统一采用奶油手账风格与等宽数字，禁用/删除沿用暖朱砂二次确认弹窗。
- **`MaidChatDrawer.vue`（全局右下角陪伴抽屉）**：
  - **悬浮呼出按钮**：告别刺眼 `bg-blue-600`，升级为 `bg-mint-600 hover:bg-mint-700 text-white shadow-mint` 奶油描边胶囊，左侧嵌 `32px` 圆形 `invoice-maid-2026.png` 头像，有待办提醒时右上角亮起一颗温和的蜜桃红点。
  - **抽屉头部**：彻底移除漆黑 `bg-slate-900`！改为**浅薄荷奶油渐变头（`bg-gradient-to-r from-[#E1F0D9] to-[#F7EFE4]`）配深栗墨水标题**，展示 `36px` 女仆头像 + `米尔 · 票据女仆` + 副标 `查账 · 归类 · 拍码开票`，清爽治愈。
  - **气泡配色**：用户消息气泡用 `bg-mint-600 text-white`，米尔回复气泡用 `bg-cream-paper border border-cream-border text-ink-900`；工具调用事件（`⚡ search_invoices`）改为温润的燕麦小徽章；空对话态提供 3 个**可直接点击发送的快捷指令芯片**，零输入门槛。

---

## 五、女仆微文案与交互语调手册（Voice & Tone）

### 5.1 语调黄金比例
- **50% 细心可靠**：数据、金额、数量、报错原因必须准确清晰，不卖萌掩盖关键信息。
- **35% 温柔鼓励**：安抚报销与对账时的烦躁情绪，给予「已经妥当」「别担心」的确定感。
- **15% 认真小迷糊**：仅在非关键的轻提示、空状态或求助场景点缀轻微反差萌（如「差一点点」「我再去翻一遍」「这个验证码把我难住啦」），**绝不在严重故障或资金核对出错时开玩笑**。

### 5.2 避坑红线（Don'ts）
- ❌ 禁止过度网络烂梗、颜文字刷屏（`QwQ` / `QAQ` / `呜呜呜`）或油腻称呼（避免过度使用「主人大人」等让人在办公室社死的词，统一使用亲切自然的 **「您」**）。
- ❌ 禁止模糊报错（不要只说「出错啦请重试」，必须先安抚再给出**具体原因 + 下一步动作**）。
- ❌ 禁止中英混杂的半吊子文案。

### 5.3 典型场景实景文案对照表

| 场景 | 现有旧文案 | 推荐女仆微文案（以首推名「米尔」为例） |
| :--- | :--- | :--- |
| **首次空状态（未配邮箱）** | `Welcome to Invoice Maid. To get started, configure an email account.` | **标题**：「您好呀，我是您的票据女仆米尔～」<br>**正文**：「先把收发票的邮箱交给我吧！配好之后，新来的电子票我会每天按时帮您收齐、算好账，不用再一封封翻邮件啦。」<br>**按钮**：「去连接第一个邮箱」 |
| **已配邮箱但暂无发票** | `No Invoices Found Yet. Accounts are configured, but no invoices have been extracted.` | **标题**：「账本已经摊开好啦，正等第一张发票住进来」<br>**正文**：「邮箱通道都畅通着呢！您可以让我现在去巡检一圈，或者直接把电脑里的 PDF/OFD 发票拖进来归档。」 |
| **搜索 / 筛选无结果** | `No invoices match your search. Try adjusting your search terms...` | **标题**：「咦，米尔翻遍了这几页账本，没有找到符合条件的票据呢」<br>**正文**：「要不要放宽一下日期范围，或者清空筛选条件再让我找找看？」<br>**按钮**：「清空全部筛选」 |
| **列表 / 扫描加载中** | `Loading...` / `Scanning...` | 「正在帮您理清票据，稍等一小会儿哦…」<br>邮箱巡检中：「米尔正在逐封翻看邮箱，已经查过 24 封邮件啦…」 |
| **心跳巡检完成** | `心跳巡检完成：处理排队任务 2 个，闭环匹配新发票 1 张。` | 「巡检回来啦！刚帮您推进了 **2** 个排队中的开票任务，还顺利收到并归档了 **1** 张新发票～别担心，都记进账本里咯。」 |
| **拍码开票提交成功** | `任务已提交，等待收票` | 「妥妥的！已经按「XX 科技有限公司」的抬头替您填好表单并提交啦。接下来由我盯着邮箱，发票一到立刻自动入账～」 |
| **多抬头需要选择** | `请选择本次抬头：` | 「这张小票我已经看清商户和金额啦！不过您存了好几个开票抬头，怕我一迷糊开错了——这次要开给哪一家呢？」 |
| **遇到验证码 / 需人工介入** | `需人工介入` + 原始异常栈 | 「唔……这张开票页面弹出了图形验证码，我盯着看了半天还是怕输错 >_< 请您对照左边的页面截图填一下验证码，后面的收尾工作继续交给我！」 |
| **低置信度提醒（详情页）** | 仅显示黄色百分比 `68.0%` | 「这张票面字迹稍微有点模糊（识别把握 68%），米尔不太敢打包票，麻烦您对照左侧原件帮忙瞄一眼金额和日期哦。」 |
| **重复发票上传（409）** | `Already on file. An invoice with the same number is already saved.` | 「这张发票已经在账本里安心躺着啦（发票号重复），米尔帮您挡下来了，不会重复记账哦。」+ 按钮 `查看原有记录` |
| **批量标记已报销完成** | 无提示直接刷新 | 「啪嗒——已为选中的 **8** 张发票整齐盖上「已报销」印章！这批账务告一段落，辛苦您啦～」 |
| **导出报销包完成** | 静默下载 | 「您的报销汇总表和原件压缩包都打包齐整啦，直接交给财务就能报销，祝一路绿灯！」 |
| **网络 / 接口报错** | `Failed to load invoice details` / `Upload failed` | 「哎呀，刚才网络信号好像打了个盹，没能连上服务器。您的数据都好好留着没丢，点一下重试我们再来一次吧。」 |

---

## 六、前端分阶段落地路线图与测试兼容守则

为保证在不破坏现有 23 个前端单元测试与 6 个 Playwright E2E 回归用例的前提下平滑焕新，建议按以下 3 个阶段推进：

### Phase 0：Design Tokens 与全局骨架（预计最快见效）
1. 落地第 3.5 节的 `frontend/src/style.css` 与 `frontend/tailwind.config.js`，引入自托管轻量字体子集。
2. 更新 `frontend/src/types/index.ts` 中的 `CATEGORY_COLORS` 与 `REIMBURSEMENT_COLORS` 为奶油手账语义色。
3. 重构 `AppLayout.vue`（左侧纤细侧栏 + 顶部轻状态栏 + 移动端底部栏）与 `MaidChatDrawer.vue`（浅薄荷奶油头 + 快捷芯片），同步将全站导航统一为中文。

### Phase 1：高频核心三页焕新
1. **`LoginView.vue` & `RegisterView.vue`**：上线双栏治愈欢迎卡 + 高清 `invoice-maid-2026.png` 插画 + 锯齿撕票底边。
2. **`InvoiceListView.vue`**：上线 3 栏账本总览带（月度透视默认折叠）、索引贴纸式 Saved Views、8 列精简财务台账（金额右对齐等宽 + 印章状态）与底部悬浮批量 Dock。
3. **`InvoiceDetailView.vue`**：升级为桌面端 50/50 左右联动核对台，顶部支持一键快捷盖章切换报销状态。

### Phase 2：工作台、上传与设置中心体验升级
1. **`MaidWorkspaceView.vue`**：上线分段式工作台、抬头管理侧滑抽屉就地维护、验证码截图并排核对卡。
2. **`InvoiceUploadView.vue`**：手账拖放区 + 与拍码开票的双向分流引导 + 温柔中文回执文案。
3. **`SettingsView.vue` & `AdminView.vue`**：左侧双分组垂直菜单重构 6 个设置面板，全站微文案切换为第五章女仆语调规范。

### 自动化测试与契约兼容守则（落地必读）
- **保留既有测试锚点**：重构 `InvoiceListView.vue`、`MultiSelectChips.vue`、`CategoryBadge.vue` 时，必须完整保留现有 `data-test-id`（如 `stats-by-category`、`stats-category-*`、`saved-view-select`、`category-filter`、`invoice-row-*`）与 `data-category` / `data-chip-value` 属性，或在同步更新对应 Playwright E2E 选择器后再合入。
- **验证闭环**：每次视觉重构阶段完成后，均需执行 `cd frontend && npm run build`、`npm run test:run` 与 `npm run test:e2e`，并在桌面端（`1440×900`）与移动端（`390×844`）双视口截屏验收对比度与布局稳定性。
