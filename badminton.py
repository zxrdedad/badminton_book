3#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浙江警察学院 羽毛球馆自动预约脚本
通过CAS认证登录，自动查询和预约羽毛球场地
"""

import requests
import re
import json
import time
import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, List
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import urllib3

sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

_cfg = load_config()

USERNAME = _cfg["username"]
PASSWORD = _cfg["password"]
RESERVE_CONFIG = _cfg["reserve"]
SCHEDULE_CONFIG = _cfg["schedule"]
API_BASE = _cfg["api_base"]
CAS_BASE = _cfg["cas_base"]
VENUE_ID = _cfg["venue_id"]
SERVICE_URL = f"{API_BASE}/cas/login"
NETWORK_MODE = _cfg.get("network_mode", "direct")

WEBVPN_BASE = "https://webvpn.zjjcxy.cn"

_WEBVPN_KEY_HEX = "909721fc475008301e68e9ccf83543551074948a88d23ec6e2"
_WEBVPN_KEY_BYTES = bytes.fromhex(_WEBVPN_KEY_HEX)
_WEBVPN_PREFIX_HEX = "77726476706e69737468656265737421"


def webvpn_encode(host: str) -> str:
    data = host.encode('ascii')
    result = bytearray()
    for i, b in enumerate(data):
        result.append(b ^ _WEBVPN_KEY_BYTES[i % len(_WEBVPN_KEY_BYTES)])
    return _WEBVPN_PREFIX_HEX + result.hex()


def _to_webvpn_url(internal_url: str) -> str:
    parsed = urlparse(internal_url)
    scheme = parsed.scheme
    host = parsed.hostname
    port = parsed.port
    path = parsed.path
    query = f"?{parsed.query}" if parsed.query else ""

    encoded_host = webvpn_encode(host)
    scheme_prefix = f"/{scheme}"

    port_suffix = f"/{port}" if port else ""
    if path.startswith("/"):
        path = path[1:]
    path_suffix = f"/{path}" if path else ""

    return f"{WEBVPN_BASE}{scheme_prefix}/{encoded_host}{port_suffix}{path_suffix}{query}"


def _webvpn_encrypt_password(password: str) -> str:
    from Crypto.Cipher import AES
    aes_key = b"wrdvpnisawesome!"
    aes_iv = b"wrdvpnisawesome!"
    padded = password + "0" * (16 - len(password) % 16) if len(password) % 16 != 0 else password
    cipher = AES.new(aes_key, AES.MODE_CFB, aes_iv, segment_size=128)
    encrypted = cipher.encrypt(padded.encode("utf-8"))
    return aes_iv.hex() + encrypted.hex()[:len(password) * 2]


class CASClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.session.verify = False
        self.token: Optional[str] = None

    def login(self, username: str, password: str) -> bool:
        logger.info("=" * 50)
        logger.info("开始CAS登录 (内网直连模式)")

        try:
            cas_login_url = f"{CAS_BASE}/cas/login?service={SERVICE_URL}"
            logger.info("[1/4] 获取CAS登录页...")
            resp = self.session.get(cas_login_url, timeout=30)
            logger.info(f"  状态码: {resp.status_code}")

            soup = BeautifulSoup(resp.text, 'html.parser')
            execution_input = soup.find("input", {"name": "execution"})
            if not execution_input:
                logger.error("未找到execution参数")
                return False
            execution = execution_input["value"]

            logger.info(f"[2/4] 提交登录 (用户: {username})")
            resp = self.session.post(
                cas_login_url,
                data={
                    "username": username,
                    "password": password,
                    "execution": execution,
                    "_eventId": "submit",
                    "geolocation": ""
                },
                allow_redirects=False,
                timeout=30
            )

            if resp.status_code != 302:
                logger.error(f"登录失败, 状态码: {resp.status_code}")
                return False

            redirect_url = resp.headers.get("Location", "")
            if "ticket=" not in redirect_url:
                logger.error("未获取到Ticket")
                return False
            logger.info(f"  获取Ticket成功")

            logger.info("[3/4] 用Ticket换取Token...")
            resp = self.session.get(redirect_url, allow_redirects=False, timeout=30)

            logger.info("[4/4] 解析Token...")
            match = re.search(
                r"""setCookie\(["']EL-ADMIN-TOEKN["'],["'](Bearer .*?)["']\)""",
                resp.text
            )
            if not match:
                match = re.search(
                    r"""["']EL-ADMIN-TOEKN["'],["'](Bearer .*?)["']""",
                    resp.text
                )

            if not match:
                logger.error("解析Token失败")
                logger.debug(f"响应内容: {resp.text[:500]}")
                return False

            self.token = match.group(1)
            self._set_auth_headers('https://attendence.yyhj.zjjcxy.cn')
            logger.info(f"  Token: {self.token[:40]}...")

            if self._verify():
                logger.info("登录验证通过!")
                return True
            else:
                logger.warning("Token验证失败, 但继续尝试")
                return True

        except requests.exceptions.ConnectionError as e:
            logger.error(f"连接失败: {e}")
            return False
        except Exception as e:
            logger.error(f"登录异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _set_auth_headers(self, origin: str):
        self.session.headers.update({
            'Authorization': self.token,
            'Origin': origin,
            'Referer': origin + '/',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json'
        })

    def _verify(self) -> bool:
        try:
            url = f"{API_BASE}/api/reserveSessions/query"
            resp = self.session.get(
                url,
                params={'venueId': VENUE_ID, 'day': datetime.now().strftime('%Y-%m-%d')},
                timeout=10
            )
            return resp.status_code == 200
        except:
            return False

    def get(self, url: str, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.session.post(url, **kwargs)


class WebVPNCASClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        self.session.verify = False
        self.token: Optional[str] = None

        self.api_vpn_base = _to_webvpn_url(API_BASE)
        self.cas_vpn_base = _to_webvpn_url(CAS_BASE)
        self.service_vpn_url = _to_webvpn_url(SERVICE_URL)
        self.origin_vpn = _to_webvpn_url('https://attendence.yyhj.zjjcxy.cn')

    def login(self, username: str, password: str) -> bool:
        logger.info("=" * 50)
        logger.info("开始WebVPN代理模式登录")

        if not self._login_webvpn(username, password):
            return False

        if not self._login_cas(username, password):
            return False

        return True

    def _login_webvpn(self, username: str, password: str) -> bool:
        logger.info("[WebVPN] 步骤1: 登录WebVPN...")
        try:
            resp = self.session.get(f"{WEBVPN_BASE}/login", timeout=30)

            captcha_id_match = re.search(r'name="captcha_id"\s+value="([^"]+)"', resp.text)
            if not captcha_id_match:
                logger.error("无法获取WebVPN captcha_id")
                return False
            captcha_id = captcha_id_match.group(1)

            need_captcha_match = re.search(r'name="needCaptcha"\s+value="([^"]+)"', resp.text)
            need_captcha = need_captcha_match and need_captcha_match.group(1) == "true"

            encrypted_pwd = _webvpn_encrypt_password(password)

            data = [("username", username), ("password", encrypted_pwd), ("captcha_id", captcha_id)]
            if need_captcha:
                logger.warning("WebVPN需要验证码!")
                captcha_url = f"{WEBVPN_BASE}/captcha/{captcha_id}.png"
                resp = self.session.get(captcha_url, timeout=30)
                captcha_path = os.path.join(BASE_DIR, "captcha.png")
                with open(captcha_path, "wb") as f:
                    f.write(resp.content)
                captcha_code = input("请查看 captcha.png 并输入验证码: ").strip()
                data.append(("captcha", captcha_code))

            resp = self.session.post(f"{WEBVPN_BASE}/do-login", data=data, timeout=30)
            try:
                result = resp.json()
            except Exception:
                logger.error(f"WebVPN登录响应异常: {resp.text[:200]}")
                return False

            if not result.get("success"):
                error = result.get("error", "")
                message = result.get("message", "")
                if error == "NEED_CONFIRM":
                    logger.info("账号在其他设备登录, 正在确认...")
                    resp = self.session.post(f"{WEBVPN_BASE}/do-confirm-login", timeout=30)
                    try:
                        if resp.json().get("success"):
                            logger.info("WebVPN登录成功!")
                        else:
                            logger.error("确认登录失败")
                            return False
                    except Exception:
                        logger.error("确认登录失败")
                        return False
                else:
                    logger.error(f"WebVPN登录失败: [{error}] {message}")
                    return False
            else:
                logger.info("WebVPN登录成功!")
                redirect_url = result.get("url", "/")
                if redirect_url.startswith("/"):
                    redirect_url = WEBVPN_BASE + redirect_url
                self.session.get(redirect_url, timeout=30)

            return True
        except Exception as e:
            logger.error(f"WebVPN登录异常: {e}")
            return False

    def _login_cas(self, username: str, password: str) -> bool:
        logger.info("[WebVPN] 步骤2: CAS登录...")
        try:
            cas_login_url = f"{self.cas_vpn_base}/cas/login?service={SERVICE_URL}"
            logger.info(f"  获取CAS登录页...")
            logger.info(f"  CAS URL: {cas_login_url[:120]}")
            resp = self._follow_redirects(cas_login_url)
            logger.info(f"  状态码: {resp.status_code}, URL: {resp.url[:120]}")

            soup = BeautifulSoup(resp.text, 'html.parser')
            execution_input = soup.find("input", {"name": "execution"})
            if not execution_input:
                logger.error("未找到execution参数")
                logger.debug(f"页面内容(前500): {resp.text[:500]}")
                return False
            execution = execution_input["value"]

            logger.info(f"  提交CAS登录 (用户: {username})")
            cas_post_url = resp.url
            resp = self.session.post(
                cas_post_url,
                data={
                    "username": username,
                    "password": password,
                    "execution": execution,
                    "_eventId": "submit",
                    "geolocation": ""
                },
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer': cas_post_url,
                },
                allow_redirects=False,
                timeout=30
            )

            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get("Location", "")
                logger.info(f"  CAS重定向: {redirect_url[:120]}")
                if redirect_url:
                    if redirect_url.startswith("/"):
                        redirect_url = urljoin(resp.url, redirect_url)
                    if not redirect_url.startswith(WEBVPN_BASE):
                        redirect_url = _to_webvpn_url(redirect_url)
                    resp = self._follow_redirects(redirect_url)
            elif resp.status_code != 200:
                logger.error(f"  CAS登录失败, 状态码: {resp.status_code}")
                return False

            logger.info("  解析Token...")
            match = re.search(
                r"""setCookie\(["']EL-ADMIN-TOEKN["'],["'](Bearer .*?)["']\)""",
                resp.text
            )
            if not match:
                match = re.search(
                    r"""["']EL-ADMIN-TOEKN["'],["'](Bearer .*?)["']""",
                    resp.text
                )

            if not match:
                logger.error("解析Token失败")
                logger.debug(f"响应内容(前500): {resp.text[:500]}")
                return False

            self.token = match.group(1)
            self._set_auth_headers(self.origin_vpn)
            logger.info(f"  Token: {self.token[:40]}...")

            if self._verify():
                logger.info("WebVPN登录验证通过!")
            else:
                logger.warning("Token验证失败, 但继续尝试")
            return True

        except Exception as e:
            logger.error(f"CAS登录异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _follow_redirects(self, url: str, max_redirects: int = 10) -> requests.Response:
        html_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        resp = self.session.get(url, headers=html_headers, timeout=30, allow_redirects=False)
        for i in range(max_redirects):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("Location", "")
            if not location:
                break
            if location.startswith("/"):
                location = urljoin(resp.url, location)
            if location.startswith(WEBVPN_BASE):
                logger.info(f"  重定向[{i+1}] (WebVPN): {location[:120]}")
                resp = self.session.get(location, headers=html_headers, timeout=30, allow_redirects=False)
            elif any(d in location for d in ("zjjcxy.cn",)):
                vpn_url = _to_webvpn_url(location)
                logger.info(f"  重定向[{i+1}]: {location[:80]} -> {vpn_url[:80]}")
                resp = self.session.get(vpn_url, headers=html_headers, timeout=30, allow_redirects=False)
            else:
                logger.info(f"  重定向[{i+1}] 跳出(外部): {location[:120]}")
                break
        return resp

    def _set_auth_headers(self, origin: str):
        self.session.headers.update({
            'Authorization': self.token,
            'Origin': origin,
            'Referer': origin + '/',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json'
        })

    def _verify(self) -> bool:
        try:
            url = f"{self.api_vpn_base}/api/reserveSessions/query"
            resp = self.session.get(
                url,
                params={'venueId': VENUE_ID, 'day': datetime.now().strftime('%Y-%m-%d')},
                timeout=10
            )
            return resp.status_code == 200
        except:
            return False

    def get(self, url: str, **kwargs):
        vpn_url = _to_webvpn_url(url) if not url.startswith(WEBVPN_BASE) else url
        return self.session.get(vpn_url, **kwargs)

    def post(self, url: str, **kwargs):
        vpn_url = _to_webvpn_url(url) if not url.startswith(WEBVPN_BASE) else url
        return self.session.post(vpn_url, **kwargs)


def create_client() -> CASClient:
    if NETWORK_MODE == "webvpn":
        logger.info("网络模式: WebVPN代理")
        return WebVPNCASClient()
    else:
        logger.info("网络模式: 内网直连")
        return CASClient()


class Reservation:
    def __init__(self, client: CASClient):
        self.client = client

    def get_slots(self, day: str) -> List[Dict]:
        try:
            resp = self.client.get(
                f"{API_BASE}/api/reserveSessions/query",
                params={'venueId': VENUE_ID, 'day': day},
                timeout=10
            )
            logger.info(f"查询响应: HTTP {resp.status_code}, URL: {resp.url[:150]}")
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data if isinstance(data, list) else []
                except Exception:
                    logger.error(f"响应非JSON: {resp.text[:300]}")
                    return []
            else:
                logger.warning(f"查询时段失败: HTTP {resp.status_code}, 响应: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"查询异常: {e}")
        return []

    def parse(self, s: Dict) -> Dict:
        scene = s.get('sportScene', {})
        site = s.get('sportSite', {})

        start_time = scene.get('startTime', '')
        end_time = scene.get('endTime', '')
        if start_time and len(start_time) >= 5:
            start_time = start_time[:5]
        if end_time and len(end_time) >= 5:
            end_time = end_time[:5]

        time_str = f"{start_time}-{end_time}" if (start_time and end_time) else "未知"
        show_name = scene.get('showName', '')
        site_name = site.get('name', '')

        court_num = None
        if site_name:
            for ch in site_name:
                if ch.isdigit():
                    court_num = int(ch)
                    break

        return {
            'id': s.get('id'),
            'time': time_str,
            'start': start_time,
            'end': end_time,
            'scene_name': show_name,
            'court_name': site_name,
            'court_num': court_num,
            'site_id': site.get('id'),
            'can': str(s.get('status')) == '1',
            'status': s.get('status'),
        }

    def find_targets(self, slots: List[Dict], config: Dict) -> List[Dict]:
        parsed = [self.parse(s) for s in slots]
        available = [p for p in parsed if p['can']]

        target_time = config.get('target_time', '').strip()
        target_scene = config.get('target_scene', '').strip()
        preferred_court = config.get('preferred_court')

        candidates = available

        if target_time:
            matched = [p for p in candidates if target_time in p['time']]
            if matched:
                candidates = matched
                logger.info(f"时间匹配 '{target_time}': {len(candidates)} 个")

        if target_scene:
            matched = [p for p in candidates if target_scene in p['scene_name']]
            if matched:
                candidates = matched
                logger.info(f"场次匹配 '{target_scene}': {len(candidates)} 个")

        if preferred_court is not None:
            matched = [p for p in candidates if p['court_num'] == preferred_court]
            if matched:
                candidates = matched
                logger.info(f"场地匹配 {preferred_court}号: {len(candidates)} 个")

        return candidates

    def book(self, session_id: int) -> Tuple[bool, str]:
        try:
            resp = self.client.post(
                f"{API_BASE}/api/sportReserve",
                json={'id': session_id},
                timeout=10
            )
            logger.debug(f"预约响应: HTTP {resp.status_code} - {resp.text[:200]}")

            if resp.status_code in (200, 201):
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        if data.get('id') or data.get('success'):
                            return True, "预约成功!"
                        msg = data.get('message', data.get('msg', resp.text[:100]))
                        return False, msg
                    return True, "预约成功!"
                except:
                    return True, f"预约请求已发送 (HTTP {resp.status_code})"
            elif resp.status_code == 400:
                try:
                    err = resp.json()
                    msg = err.get('message', err.get('msg', resp.text[:100]))
                except:
                    msg = resp.text[:100]
                return False, msg
            else:
                return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            return False, str(e)


def wait_until(target_time: str):
    now = datetime.now()
    target = datetime.strptime(target_time, "%H:%M:%S").replace(
        year=now.year, month=now.month, day=now.day
    )
    if target < now:
        target += timedelta(days=1)

    diff = (target - now).total_seconds()
    logger.info(f"等待至 {target.strftime('%H:%M:%S')} (还需 {int(diff)}秒)")

    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        if remaining > 10:
            time.sleep(1)
        else:
            time.sleep(0.1)

    logger.info("时间到!")


def print_slots_table(parsed: List[Dict], title: str = ""):
    if title:
        print(f"\n{'=' * 70}")
        print(f"  {title}")
        print(f"{'=' * 70}")

    print(f"{'序号':<5} {'状态':<5} {'时段':<15} {'场次':<8} {'场地':<12} {'ID':<10}")
    print("-" * 70)
    for i, p in enumerate(parsed, 1):
        mark = "[+]" if p['can'] else "[X]"
        court = f"{p['court_num']}号场" if p['court_num'] else p['court_name'] or '-'
        print(f"{i:<5} {mark:<5} {p['time']:<15} {p['scene_name']:<8} {court:<12} {p['id']:<10}")
    print(f"{'=' * 70}")


def do_reserve(reservation: Reservation, config: Dict) -> bool:
    days_offset = config.get('days_offset', 3)
    day = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")

    logger.info(f"查询 {day} 的场地...")
    slots = reservation.get_slots(day)
    if not slots:
        logger.error("未获取到时段数据")
        return False

    parsed = [reservation.parse(s) for s in slots]
    print_slots_table(parsed, f"{day} 羽毛球馆时段列表")

    available = [p for p in parsed if p['can']]
    if not available:
        logger.warning("没有可预约时段")
        return False

    targets = reservation.find_targets(slots, config)
    if not targets:
        logger.warning("没有匹配目标的可预约时段")
        logger.info("可用时段:")
        for p in available:
            logger.info(f"  {p['scene_name']} {p['time']} {p['court_name']}")
        return False

    target = targets[0]
    court_str = f"{target['court_num']}号场" if target['court_num'] else target['court_name']
    logger.info(f"选择: {court_str} | {target['scene_name']} | {target['time']} (id={target['id']})")

    ok, msg = reservation.book(target['id'])
    if ok:
        logger.info(f"预约成功! {msg}")
    else:
        logger.error(f"预约失败: {msg}")
    return ok


def do_schedule_reserve(reservation: Reservation, config: Dict):
    run_time = SCHEDULE_CONFIG['run_time']
    advance = SCHEDULE_CONFIG['advance_login']
    max_retries = SCHEDULE_CONFIG['max_retries']
    retry_interval = SCHEDULE_CONFIG['retry_interval']

    now = datetime.now()
    rt = datetime.strptime(run_time, "%H:%M:%S").replace(
        year=now.year, month=now.month, day=now.day
    )
    login_dt = rt - timedelta(seconds=advance)

    logger.info(f"定时预约模式")
    logger.info(f"  登录时间: {login_dt.strftime('%H:%M:%S')}")
    logger.info(f"  抢约时间: {run_time}")
    logger.info(f"  本机时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    if rt < now:
        rt += timedelta(days=1)
        login_dt = rt - timedelta(seconds=advance)
        logger.info(f"  抢约时间已过, 自动调整到明天")
        logger.info(f"  登录时间: {login_dt.strftime('%H:%M:%S')}")
        logger.info(f"  抢约时间: {rt.strftime('%H:%M:%S')}")

    if login_dt > now:
        wait_until(login_dt.strftime("%H:%M:%S"))
    else:
        logger.info("  登录时间已过, 立即登录")

    client = reservation.client
    if not client.login(USERNAME, PASSWORD):
        logger.error("登录失败, 退出")
        return

    wait_until(run_time)

    days_offset = config.get('days_offset', 3)
    day = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")
    target_time = config.get('target_time', '')
    target_scene = config.get('target_scene', '')
    preferred_court = config.get('preferred_court')
    logger.info(f"开始抢约 {day} 的场地!")
    logger.info(f"  目标: {target_scene} {target_time}" + (f" {preferred_court}号场" if preferred_court else ""))

    for i in range(max_retries):
        logger.info(f"第 {i + 1}/{max_retries} 次尝试")

        slots = reservation.get_slots(day)
        targets = reservation.find_targets(slots, config)

        if targets:
            target = targets[0]
            court_str = f"{target['court_num']}号场" if target['court_num'] else target['court_name']
            logger.info(f"选择: {court_str} | {target['scene_name']} | {target['time']}")

            ok, msg = reservation.book(target['id'])
            if ok:
                logger.info(f"预约成功! {msg}")
                return
            logger.warning(f"失败: {msg}")
        else:
            logger.warning("暂无匹配的可用时段")

        time.sleep(retry_interval)

    logger.error(f"已达到最大重试次数 ({max_retries}), 预约失败")


def main():
    print("=" * 50)
    print("  浙江警察学院 羽毛球馆自动预约")
    print("=" * 50)
    print(f"  本机时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    print("1. 立即预约")
    print("2. 交互式预约")
    print("3. 定时预约")
    m = input("\n选项: ").strip()

    if m == '3':
        client = create_client()
        reservation = Reservation(client)
        do_schedule_reserve(reservation, RESERVE_CONFIG)

    elif m == '2':
        client = create_client()
        if not client.login(USERNAME, PASSWORD):
            return

        reservation = Reservation(client)
        d = input("\n日期 (YYYY-MM-DD, 回车=3天后): ").strip()
        if not d:
            d = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

        slots = reservation.get_slots(d)
        if not slots:
            print("未获取到时段数据")
            return

        parsed = [reservation.parse(s) for s in slots]
        print_slots_table(parsed, f"{d} 羽毛球馆")

        avail = [p for p in parsed if p['can']]
        if not avail:
            print("没有可预约时段")
            return

        c = input("\n输入序号预约: ").strip()
        if c.isdigit() and 1 <= int(c) <= len(parsed):
            p = parsed[int(c) - 1]
            court_str = f"{p['court_num']}号场" if p['court_num'] else p['court_name']
            print(f"\n预约: {court_str} | {p['scene_name']} | {p['time']} (ID: {p['id']})")
            ok, msg = reservation.book(p['id'])
            print(f"\n{'成功' if ok else '失败'}: {msg}")

    else:
        client = create_client()
        if not client.login(USERNAME, PASSWORD):
            return

        reservation = Reservation(client)
        do_reserve(reservation, RESERVE_CONFIG)


if __name__ == '__main__':
    main()
