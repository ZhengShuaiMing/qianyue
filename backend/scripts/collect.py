"""在终端里自己发起岗位采集，不经过助手。后端要先启动（uv run uvicorn app.main:app --port 8000）。

用法（在 backend/ 目录下）：

    uv run python scripts/collect.py 前端开发 北京 boss:15 51job:20
    uv run python scripts/collect.py 前端开发,React 北京,上海 zhaopin
    uv run python scripts/collect.py 前端开发 北京 boss:10 --min-salary 20 --years 4 \\
        --exclude 实习,外包
    uv run python scripts/collect.py 前端开发 北京 zhaopin:5 --evaluate
    uv run python scripts/collect.py --login liepin

前两个参数是关键词和城市，多个用逗号隔开。后面是平台，写成「平台:数量」，数量是每个搜索
新增多少个岗位（1–20），不写就是 10。平台：51job 前程无忧，liepin 猎聘，zhaopin 智联招聘，
boss BOSS 直聘。几个平台在一次采集里按顺序做，每个平台用自己的数量。

采集在后台进行，不弹出浏览器。平台要求登录或验证时，那个页面会自动在专用浏览器里打开，
处理好以后用同样的命令再运行一次，已经保存的岗位不会重复读取。
--min-salary、--years、--exclude 是筛选条件，不符合的岗位不读也不保存。--evaluate 采完自动评估。
--list-only 不读详情页，只存列表上的信息：不想登录猎聘、BOSS 直聘时用，这样存下的岗位
标为「JD 不完整」，补上 JD 后才能评估。
--login 只打开一个平台让你提前登录，不采集。中途按 Ctrl+C 会停下正在进行的采集。
"""

import argparse
import json
import sys

import httpx

NAMES = {"51job": "前程无忧", "liepin": "猎聘", "zhaopin": "智联招聘", "boss": "BOSS 直聘"}


def problem(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text
    if isinstance(detail, list):  # 参数校验没过时是一个列表
        return "；".join(
            str(item.get("msg", item)).removeprefix("Value error, ") for item in detail
        )
    return str(detail)


def collect(client: httpx.Client, plan: dict[str, int], options: dict) -> bool:
    scope = {"platforms": list(plan), "per_platform": plan, **options}
    response = client.post("/api/runs", json={"kind": "collection", "collection": scope})
    if response.status_code != 202:
        print(f"没有开始。{problem(response)}")
        return False
    run_id = response.json()["id"]
    try:
        with client.stream("GET", f"/api/runs/{run_id}/events", timeout=None) as events:
            name = ""
            for line in events.iter_lines():
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: ") and name == "progress":
                    print(f"  {json.loads(line[6:])['progress']}")
                elif line.startswith("data: ") and name == "run":
                    follow_up = json.loads(line[6:])
                    print(
                        f"  已经自动发起对新增的 {len(follow_up['job_ids'])} 个岗位的评估"
                        f"（运行编号 {follow_up['run_id']}），在后台进行"
                    )
    except KeyboardInterrupt:
        client.post(f"/api/runs/{run_id}/cancel")
        print("\n已经停下。保存好的岗位都还在。")
        raise
    run = client.get(f"/api/runs/{run_id}").json()
    for result in run["results"]:
        mark = "完成" if result["status"] == "done" else "没有完成"
        line = f"{result['search']}：{mark}。{result['reason']}"
        if result["saved"]:
            line += "。新增的岗位编号：" + "、".join(f"#{job_id}" for job_id in result["saved"])
        print(line)
    return run["status"] == "succeeded"


def main() -> int:
    parser = argparse.ArgumentParser(description="在终端里发起岗位采集")
    parser.add_argument("query", nargs="?", help="关键词，多个用逗号隔开")
    parser.add_argument("city", nargs="?", help="城市的中文名，多个用逗号隔开")
    parser.add_argument("platforms", nargs="*", help="平台:数量，例如 boss:15 51job:20")
    parser.add_argument("--min-salary", type=int, metavar="千元", help="最低月薪，例如 20 表示 20K")
    parser.add_argument("--years", type=int, metavar="年", help="你的工作年限，要求明显更高的跳过")
    parser.add_argument("--exclude", metavar="词", help="职位名里不要出现的词，多个用逗号隔开")
    parser.add_argument("--evaluate", action="store_true", help="采完自动评估新增的岗位")
    parser.add_argument(
        "--list-only", action="store_true", help="不读详情页，只存列表信息（不想登录平台时用）"
    )
    parser.add_argument("--login", metavar="平台", help="只打开这个平台让你登录，不采集")
    parser.add_argument("--server", default="127.0.0.1:8000", help="后端地址，默认 127.0.0.1:8000")
    args = parser.parse_args()

    with httpx.Client(base_url=f"http://{args.server}", trust_env=False, timeout=60) as client:
        try:
            if args.login:
                response = client.post("/api/collection/browser", json={"platform": args.login})
                print(response.json().get("message") or problem(response))
                return 0 if response.status_code == 200 else 1
            if not (args.query and args.city and args.platforms):
                parser.error("要给出关键词、城市和至少一个平台，例如：前端开发 北京 boss:15")
            plan: dict[str, int] = {}
            for item in args.platforms:
                platform, _, count = item.replace("：", ":").partition(":")
                if platform not in NAMES or (count and not count.isdigit()):
                    parser.error(
                        f"看不懂「{item}」。平台写成 boss、liepin、zhaopin 或 51job，可以加 :数量"
                    )
                plan[platform] = int(count or 10)
            queries = [part for part in args.query.replace("，", ",").split(",") if part.strip()]
            cities = [part for part in args.city.replace("，", ",").split(",") if part.strip()]
            exclude = [part for part in (args.exclude or "").replace("，", ",").split(",") if part]
            options = {
                "queries": queries,
                "cities": cities,
                "min_salary": args.min_salary,
                "years": args.years,
                "exclude": exclude,
                "evaluate": args.evaluate,
                "list_only": args.list_only,
            }
            print("每个搜索新增：" + "，".join(f"{NAMES[p]} {n} 个" for p, n in plan.items()))
            return 0 if collect(client, plan, options) else 1
        except httpx.ConnectError:
            print(f"连不上后端（{args.server}）。请先在 backend/ 目录下启动：")
            print("  uv run uvicorn app.main:app --port 8000")
            return 1
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    sys.exit(main())
