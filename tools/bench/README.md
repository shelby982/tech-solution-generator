# 逐帧工作台（本地工具）

这是「视频逐帧工作台」的独立页面，**不是投标系统产品页**，`backend/main.py` 不服务它。

启动方式（本地工具，不入产品部署）：

```bash
python3 scripts/bench_server.py     # http://127.0.0.1:5199/bench
```

> 注意：`scripts/bench_server.py`、`scripts/bench_render.py` 目前是本机未跟踪工具脚本；
> 若团队要长期保留该工具，应把它们连同本目录一起纳入版本库。
