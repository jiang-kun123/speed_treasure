"""掌上飞车四星大吉寻宝。仅依赖 Python 标准库。"""
import argparse
import base64
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


def write_report(report):
    def safe(value):
        return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')
    lines = ['# 每日寻宝', '', f"时间：{report['time']}", '',
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
        lines += ['', '## 奖励汇总', '']
        rewards = Counter(part.strip() for item in report['rounds']
                          for part in item['reward'].split(',') if part.strip())
        lines += [f'- {safe(name)}：获得 {count} 次' for name, count in rewards.items()]
        lines += ['', '到账提示：']
        lines += [f'- {safe(item)}' for item in dict.fromkeys(r['notice'] for r in report['rounds'])]
    if report.get('error'):
        lines += ['', '失败原因：' + safe(report['error'])]
    content = '\n'.join(lines) + '\n'
    Path('report.md').write_text(content, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as file:
            file.write(content)
    print('执行结果：' + report['status'])
    if report.get('error'):
        print('失败原因：' + report['error'])


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
    try:
        config = json.loads(raw.lstrip('\ufeff'))
    except ValueError:
        raise TaskError('配置 JSON 格式有误：请使用普通双引号和空格；下划线前不能有反斜线。建议从 auth.example.json 复制模板。') from None
    if not isinstance(config, dict):
        raise TaskError('配置必须是 JSON 对象。')
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['inspect', 'once', 'all'], default='inspect')
    parser.add_argument('--jitter', action='store_true')
    parser.add_argument('--config', help='本地 JSON 配置文件路径；省略时优先读取环境变量，再读取脚本旁的 auth.json')
    args = parser.parse_args()
    report = {'time': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'rounds': [], 'status': '失败'}
    code = 0
    try:
        config = load_config(args.config)
        client = Client(config)
        if args.jitter:
            time.sleep(random.randint(0, 600))
        run(client, args.mode, report)
    except TaskError as error:
        report['error'] = str(error)
        code = 1
    except Exception:
        # 不输出可能含令牌、Cookie 或个人信息的异常对象。
        report['error'] = '配置或响应结构出现未预期异常；请核对配置并更新脱敏抓包。'
        code = 1
    finally:
        write_report(report)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
