# 数据库迁移

迁移只允许向前追加。开发环境执行：

```bash
uv run alembic upgrade head
uv run alembic downgrade base
```

生产数据回退不得删除预算修订、确认、发布或审计；`downgrade` 仅用于阶段 0 的空库结构验证。
