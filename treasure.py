"""掌上飞车四星大吉寻宝。仅依赖 Python 标准库。"""
import argparse
import base64
import hashlib
import html
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

ENDPOINT = 'https://agw.xinyue.qq.com/amp2.WPESrv/WPEIndex'
QUERY, START, FINISH = 307086, 307070, 307085


class TaskError(Exception):
    pass


def decode_response(raw):
    try:
        outer = json.loads(raw)
        if not isinstance(outer, dict) or str(outer.get('ret')) != '0':
            raise TaskError('接口外层返回失败；请检查登录凭据或活动状态。')
        inner = outer.get('data')
        if isinstance(inner, str):
            inner = json.loads(inner)
        if not isinstance(inner, dict) or str(inner.get('ret')) != '0':
            raise TaskError('接口业务返回失败；请检查登录凭据或活动状态。')
        return inner
    except (ValueError, TypeError):
        raise TaskError('响应格式不符合抓包结构；需要重新核对接口。') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config):
        for key in ('access_token', 'openid', 'role_id'):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise TaskError('SPEED_AUTH_JSON 缺少字符串字段：' + key)
        if any('REPLACE' in config[k] for k in ('access_token', 'openid', 'role_id')):
            raise TaskError('请先替换 Secrets 中的占位符。')
        self.config = config
        area = int(config.get('area_id', 1))
        if area not in (1, 2):
            raise TaskError('area_id 需要按实际抓包填写 1 或 2。')
        self.role = {
            'device': 'pc', 'area_id': area, 'plat_id': 2,
            'partition_id': area, 'game_app_id': '',
            'role_id': config['role_id'], 'game_open_id': config['role_id'],
            'role_name': config.get('role_name', ''), 'flag': 0,
            'partition_name': config.get('partition_name', base64.urlsafe_b64encode(
                ('电信区' if area == 1 else '网通区').encode()).decode()),
        }
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, flow, extra=None):
        config = self.config
        data = {
            'user_attach': json.dumps(config.get('user_attach', {
                'nickName': '', 'avatar': ''}), ensure_ascii=False),
            'ceiba_plat_id': 'ios', 'cExtData': {},
        }
        data.update(extra or {})
        body = {
            'act_id': '22799', 'flow_id': flow, 'biz_id': 'bb',
            'role': self.role, 'data': json.dumps(data, ensure_ascii=False),
        }
        body.update(extra or {})
        headers = {
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'Origin': 'https://act.xinyue.qq.com',
            'Referer': 'https://act.xinyue.qq.com/',
            'T-APPID': '1105330667', 'T-ACCOUNT-TYPE': 'qc',
            'T-ACCESS-TOKEN': config['access_token'], 'T-OPENID': config['openid'],
            'T-MODE': 'true',
            'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X) '
                          'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
                          'GH_QQConnect GameHelper_1003/3.17.0.2.2103170002',
        }
        if config.get('cookie'):
            headers['Cookie'] = config['cookie']
        request = urllib.request.Request(
            f'{ENDPOINT}?flowId={flow}&actId=22799',
            data=json.dumps(body, ensure_ascii=False).encode(), headers=headers,
            method='POST')
        # 查询可以重试；开始和结算超时可能已被服务器处理，不能盲目重复提交。
        attempts = 3 if flow == QUERY else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(request, timeout=30) as response:
                    return decode_response(response.read().decode('utf-8'))
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if retryable and attempt + 1 < attempts:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise TaskError(f'流程 {flow} 返回 HTTP {error.code}；停止执行。') from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt + 1 < attempts:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise TaskError(f'流程 {flow} 网络异常；若为开始或结算，请先在应用确认结果再重跑。') from None


def integer(data, key):
    value = data.get(key)
    if isinstance(value, bool):
        raise TaskError('接口数值字段异常：' + key)
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise TaskError('接口缺少有效数值字段：' + key) from None
    if result < 0 or str(result) != str(value):
        raise TaskError('接口数值字段异常：' + key)
    return result


def select_map(state):
    choices = []
    for group in state.get('mapList', []):
        if str(group.get('star_level')) == '4':
            choices.extend(m for m in group.get('map_info', []) if str(m.get('daji')) == '1')
    if len(choices) != 1 or not str(choices[0].get('map_id', '')).startswith('map4_'):
        raise TaskError('没有找到唯一的四星今日大吉地图，停止执行。')
    return choices[0]


def run(client, mode, report, sleep=time.sleep, now=time.time):
    state = client.call(QUERY)
    report['initial_remaining'] = integer(state, 'remain')
    report['remaining'] = report['initial_remaining']
    if mode == 'inspect':
        target = select_map(state)
        report['map'] = target.get('name', target['map_id'])
        report['status'] = '检查完成，未消耗次数'
        return
    for _ in range(20):
        before = integer(state, 'remain')
        report['remaining'] = before
        if state.get('mapId') or integer(state, 'remainingTime') != 0:
            raise TaskError('发现尚未完成的寻宝，请先在应用完成并查看奖励，再运行。')
        if before == 0:
            report['status'] = '当天次数已用完'
            return
        target = select_map(state)
        report['map'] = target.get('name', target['map_id'])
        extra = {'starLevel': 4, 'StarLevel': 4,
                 'mapId': target['map_id'], 'MapID': target['map_id']}
        started = client.call(START, extra)
        if started.get('mapId') != target['map_id'] or any(
                str(started.get(k)) != '0' for k in ('holdMatch', 'isUnLock', 'isInProgress')):
            raise TaskError('开始寻宝返回状态与成功样本不符，请在应用确认。')
        delay = integer(started, 'expireTime') - now() + 2
        if delay < -5 or delay > 120:
            raise TaskError('结束时间与预期不符，请在应用确认。')
        sleep(max(2, delay))
        reward = client.call(FINISH)
        if str(reward.get('ams_code')) != '0' or not reward.get('package_name'):
            raise TaskError('结算未返回已确认的奖励，请在应用查看获奖记录。')
        report['rounds'].append({
            'map': report['map'], 'reward': str(reward['package_name']),
            'notice': str(reward.get('msg', '')),
            'serial': str(reward.get('serial') or reward.get('req_serial') or ''),
        })
        # 结算后等待状态同步；不能仅凭接口成功就无限循环。
        for check in range(3):
            state = client.call(QUERY)
            after = integer(state, 'remain')
            report['remaining'] = after
            if after != before:
                break
            sleep(2)
        if after != before - 1:
            raise TaskError('结算后剩余次数未按一次递减，已停止；请核对活动状态。')
        if mode == 'once':
            report['status'] = '单次寻宝完成'
            return
    raise TaskError('达到每次运行最多 20 轮的保护限制。')


def render_report(report):
    def safe(value):
        return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')
    lines = [f"## {safe(report.get('label', '账号'))}", '', f"时间：{report['time']}", '',
             f"结果：{safe(report['status'])}", '',
             f"开始次数：{report.get('initial_remaining', '未知')}；"
             f"最后查询次数：{report.get('remaining', '未知')}", '',
             f"本次确认奖励的轮数：{len(report['rounds'])}", '']
    if report.get('map'):
        lines += [f"四星今日大吉：{safe(report['map'])}", '']
    if report['rounds']:
        lines += ['| 轮次 | 地图 | 奖励 |', '|---|---|---|']
        for index, item in enumerate(report['rounds'], 1):
            lines.append(f"| {index} | {safe(item['map'])} | {safe(item['reward'])} |")
        lines += ['', '### 奖励汇总', '']
        rewards = Counter(part.strip() for item in report['rounds']
                          for part in item['reward'].split(',') if part.strip())
        lines += [f'- {safe(name)}：获得 {count} 次' for name, count in rewards.items()]
        lines += ['', '到账提示：']
        lines += [f'- {safe(item)}' for item in dict.fromkeys(r['notice'] for r in report['rounds'])]
    if report.get('error'):
        lines += ['', '失败原因：' + safe(report['error'])]
    return '\n'.join(lines) + '\n'


class RewardHistory:
    """将本月逐次奖励保存在仓库中，并把旧月份压缩为简单汇总。"""

    def __init__(self, path='rewards_history.json', now=None):
        self.path = Path(path)
        self.current_month = (now or datetime.now(timezone(timedelta(hours=8)))).strftime('%Y-%m')
        self.data = {'version': 1, 'months': {}}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError, UnicodeError):
                raise TaskError('奖励历史文件损坏，已停止写入；请检查 rewards_history.json。') from None
            if not isinstance(loaded, dict) or not isinstance(loaded.get('months'), dict):
                raise TaskError('奖励历史文件结构无效，已停止写入。')
            self.data = loaded
        if self._compact_old_months():
            self.save()

    @staticmethod
    def _items(reward):
        return [part.strip() for part in str(reward).split(',') if part.strip()]

    @classmethod
    def _summary(cls, records):
        items = Counter(item for record in records for item in record.get('items', cls._items(record.get('reward', ''))))
        return {'rounds': len(records), 'items': dict(sorted(items.items()))}

    @classmethod
    def _account_summaries(cls, records):
        grouped = {}
        for record in records:
            label = str(record.get('account', '未知账号'))
            key = str(record.get('account_key') or label)
            bucket = grouped.setdefault(key, {'label': label, 'rounds': 0, 'items': {}})
            bucket['label'] = label
            bucket['rounds'] += 1
            for name in record.get('items', cls._items(record.get('reward', ''))):
                bucket['items'][name] = int(bucket['items'].get(name, 0)) + 1
        for bucket in grouped.values():
            bucket['items'] = dict(sorted(bucket['items'].items()))
        return grouped

    def _compact_old_months(self):
        changed = False
        for month, bucket in self.data['months'].items():
            if month != self.current_month and isinstance(bucket, dict) and isinstance(bucket.get('records'), list):
                self.data['months'][month] = {
                    'summary': self._summary(bucket['records']),
                    'accounts': self._account_summaries(bucket['records']),
                }
                changed = True
        return changed

    def record_report(self, report, account_key):
        if not report.get('rounds'):
            return False
        month = str(report.get('time', ''))[:7]
        if len(month) != 7:
            raise TaskError('报告时间格式异常，无法记录奖励历史。')
        bucket = self.data['months'].setdefault(month, {'records': []})
        if 'records' not in bucket:
            # 极少数情况下补记旧月数据时保留既有汇总，不伪造逐次记录。
            summary = bucket.setdefault('summary', {'rounds': 0, 'items': {}})
            accounts = bucket.setdefault('accounts', {})
            account = accounts.setdefault(account_key, {
                'label': report.get('label', '账号'), 'rounds': 0, 'items': {}})
            for item in report['rounds']:
                summary['rounds'] = int(summary.get('rounds', 0)) + 1
                account['rounds'] = int(account.get('rounds', 0)) + 1
                for name in self._items(item.get('reward', '')):
                    summary.setdefault('items', {})[name] = int(summary.get('items', {}).get(name, 0)) + 1
                    account.setdefault('items', {})[name] = int(account.get('items', {}).get(name, 0)) + 1
            self.save()
            return True
        existing = {str(record.get('id')) for record in bucket['records']}
        changed = False
        for index, item in enumerate(report['rounds'], 1):
            record_id = item.get('serial') or hashlib.sha256(
                f"{account_key}|{report.get('time')}|{index}|{item.get('reward')}".encode('utf-8')).hexdigest()[:24]
            if record_id in existing:
                continue
            bucket['records'].append({
                'id': record_id,
                'time': report.get('time'),
                'account': report.get('label', '账号'),
                'account_key': account_key,
                'map': item.get('map', ''),
                'reward': item.get('reward', ''),
                'items': self._items(item.get('reward', '')),
            })
            existing.add(record_id)
            changed = True
        if changed:
            self.save()
        return changed

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def markdown(self):
        def safe(value):
            return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')
        lines = ['# 奖励累计', '', f'## 本月累计（{self.current_month}）', '']
        current = self.data['months'].get(self.current_month, {})
        records = current.get('records', []) if isinstance(current, dict) else []
        summary = self._summary(records)
        accounts = self._account_summaries(records)
        if accounts:
            for account in accounts.values():
                lines += [f"### {safe(account['label'])}", '',
                          f"累计确认寻宝：{account['rounds']} 次", '']
                lines += ([f"- {safe(name)}：获得 {count} 次" for name, count in account['items'].items()]
                          or ['- 本月尚未记录奖励'])
                lines.append('')
        else:
            lines += ['- 本月尚未记录奖励', '']
        lines += ['### 全部账号合计', '', f"累计确认寻宝：{summary['rounds']} 次", '']
        lines += ([f"- {safe(name)}：获得 {count} 次" for name, count in summary['items'].items()]
                  or ['- 本月尚未记录奖励'])
        lines += ['', '## 历史月份简要汇总', '']
        old_months = [month for month in self.data['months'] if month != self.current_month]
        if not old_months:
            lines.append('- 暂无历史月份记录')
        for month in sorted(old_months, reverse=True):
            bucket = self.data['months'][month]
            old = bucket.get('summary') or self._summary(bucket.get('records', []))
            details = '；'.join(f'{safe(name)} × {count}' for name, count in old.get('items', {}).items()) or '无奖励明细'
            lines.append(f"- **{safe(month)}**：{int(old.get('rounds', 0))} 次；{details}")
            old_accounts = bucket.get('accounts') or self._account_summaries(bucket.get('records', []))
            for account in old_accounts.values():
                account_details = '；'.join(
                    f'{safe(name)} × {count}' for name, count in account.get('items', {}).items()) or '无奖励明细'
                lines.append(
                    f"  - {safe(account.get('label', '未知账号'))}："
                    f"{int(account.get('rounds', 0))} 次；{account_details}")
        return '\n'.join(lines) + '\n'


def write_reports(reports, history=None):
    failed = sum(bool(r.get('error')) for r in reports)
    content = (f'# 每日寻宝\n\n已处理账号：{len(reports)}；失败账号：{failed}\n\n'
               + '\n---\n\n'.join(render_report(report) for report in reports))
    if history is not None:
        content += '\n---\n\n' + history.markdown()
    Path('report.md').write_text(content, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        # 每个账号完成后写入全部已完成结果，防止后续账号失败覆盖此前奖励。
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'w', encoding='utf-8') as file:
            file.write(content)


def normalize_pasted_json(raw):
    """仅在标准 JSON 解析失败后修复已观察到的聊天格式转义。"""
    output = []
    inside = False
    index = 0
    entities = ('&#x20;', '&#X20;', '&#32;', '&nbsp;', '&#160;', '&#xa0;', '&#xA0;')
    while index < len(raw):
        char = raw[index]
        if inside and char == '\\' and index + 1 < len(raw):
            following = raw[index + 1]
            output.append('_' if following == '_' else char + following)
            index += 2
            continue
        if char == '"':
            inside = not inside
        if not inside:
            entity = next((e for e in entities if raw.startswith(e, index)), None)
            if entity:
                output.append(' ')
                index += len(entity)
                continue
            if char.isspace():
                char = ' '
        output.append(char)
        index += 1
    return ''.join(output)


def parse_config(raw):
    raw = raw.lstrip('\ufeff')
    try:
        config = json.loads(raw)
    except ValueError:
        try:
            config = json.loads(normalize_pasted_json(raw))
        except ValueError:
            raise TaskError('配置 JSON 格式有误：已尝试修复复制产生的空格和下划线转义；请检查双引号、逗号及最外层花括号，不要带代码框。') from None
    if not isinstance(config, dict):
        raise TaskError('配置必须是 JSON 对象。')
    return config


def load_config(config_path=None):
    raw = os.environ.get('SPEED_AUTH_JSON', '')
    if config_path is not None or not raw.strip():
        path = Path(config_path) if config_path else Path(__file__).with_name('auth.json')
        if not path.is_file():
            raise TaskError('未找到配置：本地请将 auth.example.json 复制为脚本旁的 auth.json 并填写；GitHub 请设置 SPEED_AUTH_JSON Secret。')
        try:
            raw = path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            raise TaskError('无法读取配置文件，请检查文件权限并保存为 UTF-8。') from None
    return parse_config(raw)


def account_sources(paths=None):
    if paths:
        if len(paths) > 5:
            raise TaskError('最多支持 5 个账号配置文件。')
        return [(i, lambda p=p: load_config(p)) for i, p in enumerate(paths, 1)]
    sources = []
    for slot in range(1, 6):
        name = 'SPEED_AUTH_JSON' if slot == 1 else f'SPEED_AUTH_JSON_{slot}'
        raw = os.environ.get(name, '')
        if raw.strip():
            sources.append((slot, lambda raw=raw: parse_config(raw)))
    return sources or [(1, lambda: load_config())]


def run_accounts(sources, mode, client_factory=Client, runner=run, sink=write_reports, history=None):
    reports = []
    seen = set()
    for slot, loader in sources:
        report = {'time': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                  'label': f'账号 {slot}', 'rounds': [], 'status': '失败'}
        identity = None
        try:
            config = loader()
            if isinstance(config.get('label'), str) and config['label'].strip():
                report['label'] += ' — ' + config['label'].strip()[:80]
            client = client_factory(config)
            identity = (str(config.get('role_id')), str(config.get('area_id', 1)))
            if identity in seen:
                raise TaskError('此角色和大区已在前面的账号中配置，跳过重复执行。')
            seen.add(identity)
            runner(client, mode, report)
        except TaskError as error:
            report['error'] = str(error)
        except Exception:
            # 不输出可能含令牌、Cookie 或个人信息的异常对象。
            report['error'] = '配置或响应结构出现未预期异常；请核对配置并更新脱敏抓包。'
        reports.append(report)
        if history is not None and identity is not None:
            history.record_report(report, '|'.join(identity))
            sink(reports, history)
        else:
            sink(reports)
        print(f"账号 {slot} 执行结果：{report['status']}")
        if report.get('error'):
            print('失败原因：' + report['error'])
    return int(any(r.get('error') for r in reports))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['inspect', 'once', 'all'], default='inspect')
    parser.add_argument('--jitter', action='store_true')
    parser.add_argument('--config', action='append', help='本地 JSON 配置路径，可重复传入，最多 5 个；省略时读取各账号环境变量或 auth.json')
    args = parser.parse_args()
    try:
        sources = account_sources(args.config)
        history = RewardHistory()
    except TaskError as error:
        print('失败原因：' + str(error))
        return 1
    if args.jitter:
        time.sleep(random.randint(0, 600))
    return run_accounts(sources, args.mode, history=history)


if __name__ == '__main__':
    raise SystemExit(main())
