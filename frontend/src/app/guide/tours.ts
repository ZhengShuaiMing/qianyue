/** 每一页的引导。第一步高亮导航栏里这一页，说明这一页做什么；后面依次高亮页面里的控件。
    target 是要高亮的元素（CSS 选择器，集中写在这里，页面里不用打标记）；go 是这一步开始前先切到的地址
    （同一页里换个视图，例如岗位页切到看板）。页面上找不到的那一步直接跳过 */
export type GuideStep = { target?: string; go?: string; title: string; text: string }
export type GuideTour = { path: string; name: string; steps: GuideStep[] }

const nav = (path: string) => `.top-nav a[href$="${path}"]`

export const TOURS: Record<string, GuideTour> = {
  '/overview': {
    path: '/overview',
    name: '总览',
    steps: [
      { target: nav('/overview'), title: '总览', text: '求职进展汇总在这一页：顶部是处于各个阶段的岗位数量，下面依次是应聘阶段的转化率、岗位的评分分布、一段时间内的应聘趋势，以及哪个来源的岗位更合适。' },
      { target: '.ov-tiles', title: '进展一眼看清', text: '处于各个阶段的岗位有多少：岗位总数、已评估、值得投递、已投递、已面试、已获 Offer，以及未通过或放弃的。' },
      { target: '.ov-grid > .ov-card:nth-child(1)', title: '应聘阶段漏斗', text: '从添加到拿到 Offer，每一步还剩多少岗位，也就是各阶段之间的转化率。哪一步比上一步少得多，就是接下来该重点推进的地方。' },
      { target: '.ov-grid > .ov-card:nth-child(2)', title: '评分分布', text: '每个点是一个评估过的岗位。4.0 分以上的多，说明方向找对了；大多低于 3.5 时，可以调整采集的关键词，或者把材料写得更完整。' },
      { target: '.ov-card:has(.ov-card-head)', title: '节奏', text: '按天、按周或按月统计新增、投递和面试的岗位数，看你的求职在时间上的变化趋势。' },
      { target: '.ov-card:has(.ov-table)', title: '岗位来源', text: '比较各个招聘平台的岗位数量、平均评分和投递率，值得多花时间的来源一眼看出。' },
    ],
  },
  '/collect': {
    path: '/collect',
    name: '采集',
    steps: [
      { target: nav('/collect'), title: '采集', text: '从 BOSS 直聘、猎聘、前程无忧和智联招聘一次搜完岗位，去重后放进岗位列表，不用在几个平台之间来回翻。' },
      { target: '.collect-form', title: '选平台、填范围', text: '勾上要搜的平台、写好每个平台要几个，再填关键词和城市，可以加最低月薪、工作年限和排除词。' },
      { target: '.collect-submit', title: '开始采集', text: '迁跃会打开一个专用浏览器去读岗位，平台要求登录或验证时由你自己在那个浏览器里处理。可以勾上「采完自动评估」。' },
      { target: '.collect-side', title: '采集记录', text: '每次采集搜了什么、新增了哪些岗位都记在这里，没采完的可以从断点继续。' },
    ],
  },
  '/jobs': {
    path: '/jobs',
    name: '岗位',
    steps: [
      { target: nav('/jobs'), go: '/jobs', title: '岗位', text: '一个岗位一条记录。评估、定制简历、公司研究、沟通建议、面试准备、模拟面试，都围绕这条记录做。' },
      { target: '.jobs-tools', title: '把岗位放进来', text: '粘贴 JD、链接或招聘页截图添加一个岗位，或者去采集。「批量评估」一次评好几个，「能力画像」看你看中的这类岗位普遍要什么。' },
      { target: '.jobs-list-wrap', title: '岗位列表', text: '卡片上是评估分数和当前进展，上面可以按未开始、进行中、已结束筛选。点一个岗位，右边就是它的详情。' },
      { target: '.job-hero-chips', title: '改应聘阶段', text: '投了、笔试了、面完了、被拒了，点这里改，记下哪天发生的。迁跃不会自己改阶段。' },
      { target: '.job-features', title: '一个岗位的七件事', text: '岗位信息、评估、定制简历、公司研究、沟通建议、面试准备、模拟面试都在这里。每个页签里点按钮开始，做完的结果也会存进「成果」。' },
      { target: '.jobs-filter-end .seg', go: '/jobs?view=board', title: '切到看板', text: '看板按应聘阶段分列，全部岗位放在一屏里，投了哪些、走到哪一步一眼看清。点「列表」回去看详情。' },
      { target: '.board-col:nth-child(2)', title: '每列一个阶段', text: '从左到右是未投递、已投递、已笔试、已面试、已获 Offer、已结束。列头写着有几个，卡片多了在列里上下滚。' },
      { target: '.board-card', title: '拖一下就改阶段', text: '把卡片拖到另一列，或者点卡片上的「…」选「移到…」，确认日期后才改。卡片上写着走到这一步的日期，点卡片打开这个岗位。' },
    ],
  },
  '/materials': {
    path: '/materials',
    name: '材料',
    steps: [
      { target: nav('/materials'), title: '材料', text: '迁跃了解你的依据。评估、定制简历、面试准备都只用这里写的真实经历，不会替你编造。' },
      { target: '.materials .seg', title: '三份材料', text: '基础简历是必填的；STAR 经历记下做过的事；补充资料放期望薪资、到岗时间这类简历里不写的信息。' },
      { target: '.materials-doc', title: '编辑和历史版本', text: '可以自己改，也可以和助手一起写。每次保存都留一个版本，随时能翻回去看。' },
      { target: '.materials-sample-col', title: '参考写法', text: '不知道怎么写时照着这里的格式来，写得越具体，评估越准。' },
    ],
  },
  '/artifacts': {
    path: '/artifacts',
    name: '成果',
    steps: [
      { target: nav('/artifacts'), title: '成果', text: '迁跃生成过的内容都存在这里，做过的事回头都找得到。' },
      { target: '.artifacts .seg', title: '按类型找', text: '评估、定制简历、公司研究、沟通建议、面试准备、模拟面试复盘、能力画像，可以全部看或只看一类。' },
      { target: '.artifacts-list-wrap', title: '成果列表', text: '最近更新的在前。点一份，右边就是全文。' },
      { target: '.artifacts-count .btn', title: '导出', text: '把这一类（或全部）成果打包成一个 zip 下载，每份一个 Markdown 文件。单份成果也能在右边单独导出。' },
    ],
  },
  '/search': {
    path: '/search',
    name: '检索',
    steps: [
      { target: nav('/search'), title: '检索', text: '用迁跃产生的所有内容都能在这里搜到，结果按相关度从高到低排列。助手回答问题时，也会在这里查找内容。' },
      { target: '.search-row', title: '输入要找的内容', text: '比如「Kafka 消费延迟」「期望薪资」。点结果回到原文。' },
      { target: '.search-filter .seg', title: '全部或分类查看', text: '可以看全部结果，也可以只看材料、岗位、沟通、成果或助手对话里的。' },
      { target: '.search-side', title: '检索历史', text: '搜过的词留在这里，点一下再搜一次。' },
    ],
  },
  '/assistant': {
    path: '/assistant',
    name: '助手',
    steps: [
      { target: nav('/assistant'), title: '助手', text: '说出你要做的事，让助手去办：查资料、发起评估、改阶段、整理经历。迁跃的每个功能都能通过助手完成。' },
      { target: '.chat-composer', title: '告诉助手你想做什么', text: '「帮我评估这个岗位」「XX 公司的岗位已经投递了」「帮我把这段经历整理成 STAR」。截图可以直接粘贴进来。改阶段、存材料之前，助手会先问你。' },
      { target: '.assistant-list', title: '对话记录', text: '每段对话都留着，可以接着聊，也能在「检索」里搜到。' },
    ],
  },
  '/settings': {
    path: '/settings',
    name: '设置',
    steps: [
      { target: '.top-right a[href$="/settings"]', title: '设置', text: '配置 AI 模型、联网搜索、接入你自己的 Agent，以及隐私选项。' },
      { target: '.settings-cols > :first-child', title: '配置 AI 模型', text: '填上模型服务的地址、模型名和 Key，就能在迁跃里直接用 AI 功能。Key 只存在你的电脑上。' },
      { target: '.settings-cols > :nth-child(2)', title: '联网搜索', text: '公司研究、面试准备查面经时会联网搜索。默认用 DuckDuckGo，不用 Key；也可以换成博查、智谱这些需要 Key 的服务。交给 Agent（MCP）代答时，搜索仍由迁跃按这里的设置去搜，所以这里同样要设好。' },
      { target: '#mcp', title: '或者交给你的 Agent', text: '没有 Key 也行：把迁跃连到你常用的 Agent（Codex、Claude Code、Cursor 等），评估、定制简历这些就由它来完成。' },
      { target: '.settings-privacy', title: '隐私', text: '发给模型的文字默认隐藏邮箱、手机号和你加的词。截图没法隐藏，发之前自己遮一下。' },
    ],
  },
}

/** 全部引导：按导航栏从左到右走一遍，最后是右上角的设置 */
export const ALL_PAGES = ['/overview', '/collect', '/jobs', '/materials', '/artifacts', '/search', '/assistant', '/settings']

/** 全部引导走完最后一页（设置）后补的一步 */
export const CLOSING: GuideStep[] = [
  { target: '.top-help', title: '随时再看', text: '点这里可以再看一遍：只看当前这一页，或者从头走完全部页面。' },
]

/** 这一页用哪段引导 */
export const tourFor = (pathname: string): GuideTour | undefined => TOURS[`/${pathname.split('/')[1]}`]
