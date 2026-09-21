#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./.venv/bin/python scripts/local_trial.py start --lan
if [[ $? -ne 0 ]]; then
  read "?启动未完成，按回车关闭。"
fi
