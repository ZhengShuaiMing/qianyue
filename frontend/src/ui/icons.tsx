import type { ReactNode } from 'react'

// 图标：24 格、1.6 描边、圆头。只放用到的
const Svg = ({ children }: { children: ReactNode }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)

export const IconGear = () => <Svg><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></Svg>
export const IconSun = () => <Svg><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4" /></Svg>
export const IconMoon = () => <Svg><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" /></Svg>
export const IconHelp = () => <Svg><circle cx="12" cy="12" r="8.5" /><path d="M9.6 9.5a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6" /><circle cx="12" cy="16.9" r=".6" /></Svg>
export const IconSearch = () => <Svg><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></Svg>
export const IconPlus = () => <Svg><path d="M12 5v14M5 12h14" /></Svg>
// 采集岗位：一个托盘，箭头往里收
export const IconCollect = () => <Svg><path d="M12 3.5v10M8 9.5l4 4 4-4" /><path d="M4 13.5v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" /></Svg>
export const IconMore = () => <Svg><circle cx="6" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="18" cy="12" r="1" /></Svg>
export const IconTrash = () => <Svg><path d="M4.5 7h15M10 11v6M14 11v6M6.5 7l.8 11.2A2 2 0 0 0 9.3 20h5.4a2 2 0 0 0 2-1.8L17.5 7M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2" /></Svg>
export const IconClose = () => <Svg><path d="M6 6l12 12M18 6 6 18" /></Svg>
export const IconImage = () => <Svg><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><circle cx="9" cy="10" r="1.6" /><path d="m20.5 16-4.5-4.5-8.5 8" /></Svg>
export const IconArrowRight = () => <Svg><path d="M5 12h14M13 6l6 6-6 6" /></Svg>
export const IconChevron = () => <Svg><path d="m7 10 5 5 5-5" /></Svg>
export const IconTasks = () => <Svg><path d="M9 6h11M9 12h11M9 18h11" /><path d="m3.5 6 1 1 2-2M3.5 12l1 1 2-2" /><circle cx="5" cy="18" r="1" /></Svg>
export const IconCheck = () => <Svg><path d="m5 12.5 4.5 4.5L19 7.5" /></Svg>
export const IconRedo = () => <Svg><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" /><path d="M19.5 4.5v3.2h-3.2" /></Svg>
export const IconExport = () => <Svg><path d="M12 15V4M8 8l4-4 4 4" /><path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></Svg>
export const IconEye = () => <Svg><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.8" /></Svg>
export const IconCopy = () => <Svg><rect x="8.5" y="8.5" width="11" height="11" rx="2.5" /><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></Svg>
// 能力画像：一张雷达图
export const IconProfile = () => <Svg><path d="M12 3.5 20 9.3l-3 9.2H7L4 9.3z" /><path d="M12 8.5 15.3 11l-1.3 4h-4l-1.3-4z" /></Svg>
export const IconCalendar = () => <Svg><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M3.5 10h17M8 3v4M16 3v4" /></Svg>
export const IconChevronLeft = () => <Svg><path d="m14 7-5 5 5 5" /></Svg>
export const IconChevronRight = () => <Svg><path d="m10 7 5 5-5 5" /></Svg>
// 排序：最近添加是一个时钟，评分降序是从高到低的三条横线
export const IconSortRecent = () => <Svg><circle cx="12" cy="12" r="8" /><path d="M12 7.5V12l3 2" /></Svg>
export const IconSortScore = () => <Svg><path d="M4 6.5h16M4 12h11M4 17.5h6" /><path d="M19 11v8M16.5 16.5 19 19l2.5-2.5" /></Svg>
