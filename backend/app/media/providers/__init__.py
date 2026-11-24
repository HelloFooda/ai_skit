"""媒体生成厂商实现。

**厂商名只允许出现在这个包里**（`grep -rn "ark\\|volcengine\\|doubao\\|seedream" app/media`
命中的必须全在 `providers/` 下）。业务层只认 `contract.MediaProvider` 端口。

新增一家厂商 = 新建一个模块 + 在 `registry.PROVIDERS` 注册一行，其余代码不动。
"""
