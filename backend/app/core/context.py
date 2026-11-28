from contextvars import ContextVar

# ContextVar 是「跟着当前请求走」的变量：每个并发请求各有一份，互不串值，
# 不需要把编号一层层当参数传下去。不在任何请求里时取到默认值 "-"。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# 当前所属的运行编号。运行在后台任务里执行，它的每条日志都带上这个编号，
# 用 grep 运行编号就能看到一次评估的全过程。
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")
