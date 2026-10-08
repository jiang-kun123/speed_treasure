# 掌上飞车：每日四星大吉寻宝

基于 2026-09-26 提供的抓包整理。只执行免费每日寻宝，选择四星中的“今日大吉”，约 10 秒后结算，再循环至次数为零。无需手机持续在线；账号凭据失效后需要从手机重新获取并更新 Secret。

支持 1 至 5 个账号，按顺序处理。旧的单账号 Secret 继续有效。

**状态：已完成离线测试，尚未连接真实账号验证。** 项目没有保存聊天中的令牌、Cookie 或账号标识。第一次请先执行 inspect，再执行 once 验证一次完整流程。

## 1. 新建仓库并放入文件

在 GitHub 创建一个私有仓库，例如 `speed-treasure`。将本目录的内容放到仓库根目录，确保实际路径如下（不要多套一层 speed-treasure 目录）：

```text
.github/workflows/treasure.yml
treasure.py
test_treasure.py
auth.example.json
README.md
.gitignore
```

GitHub 上传 ZIP 不会自动解压成项目。需要解压后上传文件。若上传界面不包含隐藏的 `.github` 目录，可在仓库里用 Add file → Create new file，文件名填写 `.github/workflows/treasure.yml`，复制同名文件内容保存。

## 2. 设置登录凭据

进入仓库 Settings → Secrets and variables → Actions → New repository secret。

名称：`SPEED_AUTH_JSON`。值填写 `auth.example.json` 的结构，并替换占位符：

添加其他账号时分别新建以下 Secrets，每个值都是一个账号的完整 JSON 对象，不需要合并成数组；没有配置的账号会跳过。

| 账号 | Secret 名称 |
|---|---|
| 1（原账号） | SPEED_AUTH_JSON |
| 2 | SPEED_AUTH_JSON_2 |
| 3 | SPEED_AUTH_JSON_3 |
| 4 | SPEED_AUTH_JSON_4 |
| 5 | SPEED_AUTH_JSON_5 |

可以在每个对象中添加 `"label": "大号"` 等备注，Summary 按账号序号和备注分别展示奖励。备注不要填写密码或令牌。某个账号的 JSON 错误、登录失效或寻宝失败后，会继续执行其他账号；只要有一个账号失败，最终工作流就标记为失败。重复填写相同角色和大区时会跳过后一个并报错。

从单账号版本升级时，必须同时替换 `treasure.py` 和 `.github/workflows/treasure.yml`，否则新增 Secrets 不会传入脚本。首次选择 inspect 验证所有已配置账号；**once 会让每个已配置账号各寻宝一次，all 会分别用完各账号当天次数。**

| 字段 | 来源 |
|---|---|
| label | 可选，自定义账号备注 |
| access_token | 新抓包 POST 请求头里的 T-ACCESS-TOKEN |
| openid | 同一账号同一次抓包请求头里的 T-OPENID |
| role_id | 请求正文 role.role_id，保留为字符串 |
| area_id | 请求正文 role.area_id；现有样本为 1 |
| role_name | 请求正文 role.role_name，按原值复制，通常是编码后的文字 |
| partition_name | 请求正文 role.partition_name，按原值复制 |
| user_attach | data 内 user_attach 解码后的对象，含 nickName 和 avatar；可以先保留空值 |

只复制值，不要把反斜线转义、Markdown 链接或 `&#x20;` 一起复制进去。不要把填好的配置提交到仓库，也不要把完整凭据贴回聊天。

代码使用已观察到的 T-* 登录请求头。是否还依赖 Cookie、非空 user_attach，需要首次 inspect/once 确认；如需要，可以在这个 JSON Secret 中加入 `cookie` 字符串，并填写同次抓包的完整 Cookie。不要在未验证登录凭据时不断尝试重跑。

## 3. 首次手动检查

### 在本地 PowerShell 运行

先把脚本旁的 `auth.example.json` 复制为 `auth.json`，用文本编辑器填写上述账号信息并保存为 UTF-8。不要在 Python 代码里粘贴 JSON；不要给下划线加反斜线。

```powershell
Copy-Item -LiteralPath .\auth.example.json -Destination .\auth.json
notepad .\auth.json
& 'D:\新python\python.exe' .\treasure.py --config .\auth.json --mode inspect
```

复制命令只在第一次、尚无 auth.json 时执行，以免覆盖已填写的配置。`--config` 明确指定文件时优先使用该文件；省略时先读取 `SPEED_AUTH_JSON` 环境变量，再读取脚本旁的 `auth.json`。GitHub Secrets 不会自动同步到本地电脑。

确认检查成功后，将 `--mode inspect` 改成 `--mode once` 可执行一轮。`auth.json` 已被 .gitignore 排除，但手动网页上传时仍需自己排除该文件。

本地多个账号可分别保存为 auth.json、auth2.json 等，使用 `python treasure.py --config auth.json --config auth2.json --mode inspect`。最多指定 5 个配置文件；显式指定文件时不读取其他账号的环境变量。所有真实配置只保存在本地或 Secret 中，不能上传。

### 在 GitHub 运行

Actions → 每日四星大吉寻宝 → Run workflow → mode 选择 `inspect`。

该模式只查询地图和次数，不启动或结算寻宝。成功后，在运行页面 Summary 查看剩余次数及四星大吉地图。

确认与手机页面一致，再运行 `once`，会消耗一次免费寻宝次数。结束后核对奖励和次数；通过后可以运行 `all` 用完当天次数。

## 4. 定时执行与通知

默认北京时间 06:03 触发，再随机延迟 0 至 10 分钟；GitHub 调度还可能额外延迟。每日模式为 all。上传到默认分支并设置好 Secret 后，定时任务会自动生效。

在 GitHub Settings → Notifications → System → Actions 启用 Email，并选 Only notify for failed workflows。脚本在登录失败、接口异常或次数变化不符时返回非零退出码，使该次 Actions 失败。GitHub 自带邮件主要通知运行状态；**每日奖励明细在 Actions 的运行 Summary 中查看，不会主动另发奖励邮件。**

本地运行会生成 report.md，GitHub 运行同时写入 GITHUB_STEP_SUMMARY；即使中途失败，也保留此前确认过的奖励。奖励汇总按“某个奖励组合中的项目出现几次”统计，例如“150点券：获得 3 次”。

## 5. 本月累计与历史月份

每次结算成功后，脚本会把奖励写入仓库根目录的 `rewards_history.json`。同一条结算流水不会重复计数。Actions Summary 会同时展示：

- 本次运行每一轮获得的奖励；
- 当前月份累计确认的寻宝次数和各奖励出现次数；
- 以前月份的简单汇总。

进入新月份后，旧月份的逐次记录会自动压缩为月份总次数和奖励汇总，当前月份继续保留逐次记录。该功能从升级后的第一次运行开始累计，无法自动补回此前没有保存的历史奖励。

工作流需要 `contents: write` 权限，并在运行结束后由 `github-actions[bot]` 提交 `rewards_history.json`。如果保存步骤提示 403，请进入仓库 Settings → Actions → General → Workflow permissions，允许 Read and write permissions。不要把 `rewards_history.json` 加入 `.gitignore`。

仓库内同时只运行一个寻宝任务。运行期间不要在手机上同时寻宝。如果网络异常发生在开始或结算阶段，服务器可能已处理请求：脚本会停止，请先在手机查看当前状态和获奖记录再重跑。已有未完成寻宝也会停止，避免自动处理未知进度。

公开仓库 60 天无活动可能停用定时任务，因此建议使用私有仓库。工作流没有实际被调度时，不能依赖“任务失败邮件”发现漏跑。尚未实现登录凭据自动续期，失效后手动更新 Secret。

## 已确认的协议与实现假设

接口地址：`https://agw.xinyue.qq.com/amp2.WPESrv/WPEIndex`；URL 查询参数为 flowId 和 actId=22799。

| flowId | 用途 | 使用字段 |
|---|---|---|
| 307086 | 状态及地图查询 | remain、mapList、mapId、remainingTime |
| 307070 | 启动寻宝 | starLevel/StarLevel=4、mapId/MapID；响应 expireTime |
| 307085 | 结算及奖励 | ams_code、package_name、msg |

外层 data 是 JSON 字符串，需要再次解码。307070 的大小写参数同时保留在外层和内层，与样本一致。307085 不带地图或回合标识，按已提供的请求结构构建。307086 的请求正文未单独提供，暂按相同活动通用模板构建，首次 inspect 将验证这个假设。

不使用 307069 的 remain：同一时间样本该值为 4，页面及 307086 为 3。地图从 star_level=4 的 map_info 中动态选择 daji=1 的条目，不固定地图编号。

开始请求的三个状态标记需与成功样本一致；结束时间最多允许在未来 120 秒内。结算后次数应减一，未更新会只重试查询两次，仍不一致就停止。最多执行 20 轮。查询网络故障可重试，开始和结算不会自动重发。

## 离线测试

```sh
python -m unittest -v
```

测试不调用线上接口、不消耗寻宝次数，涵盖动态选图、次数归零、只检查模式、次数不变、已有寻宝、双层 JSON 和不确定请求不重复提交。

GitHub 官方参考：[工作流摘要](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands)、[通知设置](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications)、[定时任务说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)。
