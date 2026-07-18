"""
台账新鲜度监控 —— 防「管线静默死亡」。

设计针对的是一种具体的失败形态：**工作流报告"成功"，但根本没产出任何数据。**
这种情况下所有"绿灯"都是假的，而缺口要等到有人偶然去看台账才会被发现。

所以本检查的判据是【台账数据本身的最新日期】，而不是任何工作流的自我报告；
并且它以**非零退出码**失败，从而触发 GitHub Actions 原生的失败通知邮件
（而不是往一个没人看的频道推消息）。

退出码：0 = 新鲜或尚未开账；1 = 台账过期，需要人立刻去看。
"""
from __future__ import annotations
import sys, csv, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
cfg = yaml.safe_load(open(ROOT / "config.yaml"))
LEVELS = ROOT / "data/ledger/index_level.csv"


def main():
    inception = cfg["meta"].get("inception_date")
    if not inception:
        print("尚未设定开账日 —— 无需监控。"); return 0

    today = dt.date.today()
    inc = dt.date.fromisoformat(str(inception))
    if today < inc:
        print(f"未到开账日（{inception}）—— 无需监控。"); return 0

    max_stale = cfg.get("monitoring", {}).get("max_stale_days", 4)

    # 开账日当天或之后，台账文件必须存在
    if not LEVELS.exists():
        days = (today - inc).days
        if days > max_stale:
            print(f"🚨 已过开账日 {inception} 共 {days} 天，台账文件仍不存在。"
                  f"\n   开账很可能失败了，且没有任何人发现。")
            return 1
        print(f"开账日 {inception} 刚过 {days} 天，台账尚未生成 —— 暂不告警。")
        return 0

    rows = list(csv.DictReader(open(LEVELS)))
    if not rows:
        print("🚨 台账文件存在但没有任何记录 —— 管线可能空转。")
        return 1

    last = dt.date.fromisoformat(rows[-1]["date"])
    stale = (today - last).days
    if stale > max_stale:
        print(f"🚨 台账已 {stale} 天未更新（最后一行 {last}，阈值 {max_stale} 天）。"
              f"\n   共 {len(rows)} 行记录。管线可能已静默死亡——"
              f"工作流也许一直显示成功，但没有产出数据。"
              f"\n   请立刻检查 daily.yml 的最近几次运行。")
        return 1

    print(f"✅ 台账新鲜：最后更新 {last}（{stale} 天前），共 {len(rows)} 行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
