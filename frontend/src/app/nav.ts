/** 顶栏的主导航：先看总览，再采集、处理岗位，材料是依据，成果是产出，
    检索在这些内容里一起找，助手是另一种入口。
    设置不在这里，在右边的齿轮按钮 */
export const NAV = [
  { to: '/overview', label: '总览' },
  { to: '/collect', label: '采集' },
  { to: '/jobs', label: '岗位' },
  { to: '/materials', label: '材料' },
  { to: '/artifacts', label: '成果' },
  { to: '/search', label: '检索' },
  { to: '/assistant', label: '助手' },
]

/** 浏览器标签页上的页面名 */
export const PAGE_NAMES: Record<string, string> = {
  ...Object.fromEntries(NAV.map((n) => [n.to, n.label])),
  '/settings': '设置',
}
