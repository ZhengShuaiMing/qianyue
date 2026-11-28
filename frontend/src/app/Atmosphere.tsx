import './atmosphere.css'

/** 背景：三团很淡的大光合起来铺满页面，慢慢漂移、互相挤、各自呼吸；上面一层很轻的胶片颗粒 */
export function Atmosphere() {
  return (
    <>
      <div className="atmo" aria-hidden="true">
        {[1, 2, 3].map((n) => <i key={n} className={`cloud cloud-${n}`}><i /></i>)}
        <i className="atmo-spot" />
      </div>
      <div className="atmo-grain" aria-hidden="true" />
    </>
  )
}
