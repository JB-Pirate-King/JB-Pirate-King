#!/usr/bin/env python3
# coding: utf-8
"""
╔══════════════════════════════════════════════════════════════════╗
║   OpenCPN AIS IDS Signal Generator  v6                          ║
║   ML-Aware Attack Simulator for AIS Intrusion Detection Research ║
╠══════════════════════════════════════════════════════════════════╣
║  아키텍처                                                        ║
║  ┌──────────────────────────────────────────────────────────┐   ║
║  │  AttackPlugin (base class + registry decorator)          │   ║
║  │    ↓                                                     │   ║
║  │  SimEngine  ←→  RealTimeState  ←→  RealTimeControlWin   │   ║
║  │    ↓                                                     │   ║
║  │  SenderWorker  →  UDP Socket  →  OpenCPN                 │   ║
║  └──────────────────────────────────────────────────────────┘   ║
║  모듈 역할                                                       ║
║   AttackPlugin  : 메타데이터 + make/update 인터페이스            ║
║   AttackRegistry: 데코레이터 기반 자동 등록                      ║
║   SimEngine     : tick 루프, RT 오버라이드 적용                  ║
║   SenderWorker  : UDP 전송 스레드                                ║
║   App           : tkinter GUI (좌=설정, 우=로그)                 ║
║   RealTimeControlWindow : 별도 Toplevel 실시간 조작              ║
╠══════════════════════════════════════════════════════════════════╣
║  ML 탐지 모델 가정                                               ║
║   Feature set :                                                  ║
║    - Δsog, Δcog, Δhdg  (속도·방향 변화율)                       ║
║    - Haversine dist / Δt  (실효 속도)                            ║
║    - trajectory_continuity  (LSTM 시계열 잔차)                   ║
║    - navStatus_consistency  (상태 일관성)                        ║
║    - mmsi_cluster_feat  (DBSCAN 클러스터 내 위치)                ║
║    - ship_type_sog_residual  (선종별 SOG 분포 잔차)              ║
║    - ais_gap_duration  (신호 소실 시간)                          ║
║    - report_interval_irregularity  (Δt 분산)                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import math, queue, random, socket, threading, time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ══════════════════════════════════════════════════
#  §1  상수 / 전역
# ══════════════════════════════════════════════════
_KN_TO_DPS = 1852.0 / 111320.0 / 3600.0   # knot → deg/s
_LOG_Q: queue.Queue = queue.Queue()

# 실시간 제어 공유 상태 (메인 ↔ 송신 스레드)
@dataclass
class RealTimeState:
    active:        bool  = False
    sog_mult:      float = 1.0
    cog_offset:    float = 0.0
    pos_scatter:   float = 0.0
    nav_override:  int   = -1
    manual_jump:   bool  = False
    jump_dist:     float = 0.05

RT = RealTimeState()

# 실제 MID / 선박 프로파일 데이터
_MID_POOL = [440, 441, 432, 431, 477, 413, 416, 352, 503, 538, 566]

@dataclass
class ShipProfile:
    ship_type: int
    sog_lo:    float
    sog_hi:    float
    nav_opts:  list[int]
    name_pfx:  str

_PROFILES = [
    ShipProfile(70, 10.0, 16.0, [0],    "CARGO"),
    ShipProfile(71, 10.0, 14.0, [0],    "BULK"),
    ShipProfile(72,  8.0, 12.0, [0],    "TANKER"),
    ShipProfile(60, 14.0, 22.0, [0],    "PASS"),
    ShipProfile(30,  3.0,  9.0, [0, 7], "FISH"),
    ShipProfile(36,  4.0, 14.0, [0],    "YACHT"),
    ShipProfile(52,  5.0, 11.0, [0],    "TUG"),
]

_COASTAL_HDGS = [0, 45, 90, 135, 180, 225, 270, 315]

def _qlog(msg: str, level: str = "info") -> None:
    _LOG_Q.put({"kind": "log", "message": msg, "level": level})

def _qstate(ch: str, state: str) -> None:
    _LOG_Q.put({"kind": "chan", "channel": ch, "state": state})

# ══════════════════════════════════════════════════
#  §2  NMEA 인코딩
# ══════════════════════════════════════════════════
def _nmea_cs(s: str) -> str:
    cs = 0
    for c in s: cs ^= ord(c)
    return f"{cs:02X}"

def _encode_payload(bits: list[int]) -> str:
    while len(bits) % 6: bits.append(0)
    out = []
    for i in range(0, len(bits), 6):
        v = sum(bits[i+b] << (5-b) for b in range(6))
        c = v + 48
        if c > 87: c += 8
        out.append(chr(c))
    return "".join(out)

def build_vdm(mmsi:int, lat:float, lon:float, sog:float,
              cog:float, hdg:int, nav:int=0) -> str:
    b: list[int] = []
    def p(v:int, w:int):
        for i in range(w-1,-1,-1): b.append((int(v)>>i)&1)
    p(1,6); p(0,2); p(mmsi,30); p(nav,4); p(0,8)
    p(min(1023,max(0,round(sog*10))),10); p(1,1)
    p(int(round(lon*600000))&0xFFFFFFF,28)
    p(int(round(lat*600000))&0x7FFFFFF,27)
    p(min(3600,max(0,round(cog*10))),12)
    p(min(511,max(0,hdg%360)),9); p(int(time.time())%60,6)
    p(0,2); p(0,3); p(0,1); p(0,19)
    pl = _encode_payload(b)
    body = f"AIVDM,1,1,,A,{pl},0"
    return f"!{body}*{_nmea_cs(body)}\r\n"

def build_vsd(mmsi:int, name:str) -> str:
    nm = name[:20].upper().ljust(20,"@")
    b: list[int] = []
    def p(v:int,w:int):
        for i in range(w-1,-1,-1): b.append((int(v)>>i)&1)
    def ps(s:str,w:int):
        for c in s[:w]:
            cc = ord(c); cc = cc-64 if cc>=64 else cc; p(cc,6)
    p(24,6); p(0,2); p(mmsi,30); p(0,2); ps(nm,20); p(0,8)
    pl = _encode_payload(b)
    body = f"AIVDM,1,1,,A,{pl},0"
    return f"!{body}*{_nmea_cs(body)}\r\n"

def load_nmea(path:str) -> list[str]:
    msgs = [l.strip()+"\r\n" for l in Path(path).read_text(errors="ignore").splitlines()
            if l.strip().startswith("!AIVDM")]
    if not msgs: raise ValueError("파일에 !AIVDM 문장 없음")
    return msgs

# ══════════════════════════════════════════════════
#  §3  Vessel 모델
# ══════════════════════════════════════════════════
class Vessel:
    __slots__ = ("mmsi","name","lat","lon","sog","cog","hdg","nav","_extra")
    def __init__(self, mmsi:int, name:str, nav:int=0):
        self.mmsi=mmsi; self.name=name
        self.lat=self.lon=self.sog=self.cog=0.0
        self.hdg=0; self.nav=nav; self._extra: dict[str,Any] = {}
    def pos_msg(self)->str:
        return build_vdm(self.mmsi,self.lat,self.lon,
                         self.sog,self.cog,self.hdg,self.nav)
    def name_msg(self)->str: return build_vsd(self.mmsi,self.name)
    def x(self,key:str,default=None): return self._extra.get(key,default)
    def sx(self,key:str,val): self._extra[key]=val; return val

def _mk_mmsi(mid:int|None=None)->int:
    m = mid or random.choice(_MID_POOL)
    return m*1000000+random.randint(100000,999999)

def _place(v:Vessel,clat:float,clon:float,r:float=0.08)->None:
    v.lat=clat+random.uniform(-r,r)
    v.lon=clon+random.uniform(-r*1.2,r*1.2)

def _step(v:Vessel,dt:float)->None:
    s=v.sog*_KN_TO_DPS*dt
    v.lat+=math.cos(math.radians(v.cog))*s
    v.lon+=math.sin(math.radians(v.cog))*s*1.2

def _sleep(ev:threading.Event,sec:float)->bool:
    end=time.time()+max(0,sec)
    while not ev.is_set():
        r=end-time.time()
        if r<=0: return True
        time.sleep(min(0.05,r))
    return False

# ══════════════════════════════════════════════════
#  §4  AttackPlugin 기반 + Registry
# ══════════════════════════════════════════════════
@dataclass
class AttackMeta:
    key:         str
    label:       str
    category:    str   # A B C D E F
    purpose:     str   # 어떤 feature를 속이는지
    evasion:     str   # 탐지 회피 전략 요약
    expected_fn: str   # 예상 탐지 실패 이유

class AttackPlugin(ABC):
    meta: AttackMeta

    @abstractmethod
    def make(self, cfg:dict)->list[Vessel]: ...

    @abstractmethod
    def update(self, fleet:list[Vessel], elapsed:float, dt:float, cfg:dict)->None: ...

    def param_defs(self)->list[dict]:
        """GUI 파라미터 정의: [{'label','key','type','min','max','default','step'}]"""
        return []

class AttackRegistry:
    _map: dict[str,"AttackPlugin"] = {}
    _order: list[str] = []

    @classmethod
    def register(cls, plugin_class):
        """클래스를 받아 인스턴스화 후 등록. 데코레이터에서 호출."""
        instance = plugin_class()
        k = instance.meta.key
        cls._map[k] = instance
        if k not in cls._order: cls._order.append(k)
        return plugin_class   # 클래스 자체를 반환 (데코레이터 체인 유지)

    @classmethod
    def get(cls, key:str)->"AttackPlugin": return cls._map[key]

    @classmethod
    def all(cls)->list["AttackPlugin"]:
        return [cls._map[k] for k in cls._order]

    @classmethod
    def labels(cls)->list[str]:
        return [cls._map[k].meta.label for k in cls._order]

    @classmethod
    def key_by_label(cls, lbl:str)->str:
        for k,v in cls._map.items():
            if v.meta.label==lbl: return k
        raise KeyError(lbl)

def _reg(plugin_class)->"type[AttackPlugin]":
    AttackRegistry.register(plugin_class)
    return plugin_class

# 파라미터 헬퍼
def _pi(label,key,lo,hi,default,step=1.0,t="spin"):
    return {"label":label,"key":key,"type":t,"min":lo,"max":hi,"default":default,"step":step}
def _pc(label,key,values,default):
    return {"label":label,"key":key,"type":"combo","values":values,"default":default}


# ══════════════════════════════════════════════════
#  §5  공격 플러그인 구현
#  카테고리:
#   A  규칙 기반 탐지 대상 (IDS 검증용)
#   B  시각적 패턴
#   C  규칙 IDS False-Negative
#   D  ML 우회 v1 (단일 피처 조작)
#   E  ML 우회 v2 (구조적)
#   F  고급 (LSTM·clustering·hybrid·gap)
# ══════════════════════════════════════════════════

# ─── A1  속도 이상 ────────────────────────────────
@_reg
class SpeedSpike(AttackPlugin):
    meta = AttackMeta(
        key="speed_spike", label="A1  속도 이상",
        category="A",
        purpose="SOG 상한 초과 탐지 (ship-type maxSOG)",
        evasion="없음 — IDS 정상 동작 검증용",
        expected_fn="maxSOG 초과 즉시 탐지됨")

    def param_defs(self):
        return [_pi("선박 수","count",1,200,25),
                _pi("기본 SOG (kn)","sog_base",0,40,8,0.5),
                _pi("스파이크 SOG (kn)","sog_spike",0,60,32,1.0),
                _pc("방식","mode",["간헐","순간"],"간헐"),
                _pi("스파이크 주기 (초)","interval",1,120,10,1.0)]

    def make(self,cfg):
        n=int(cfg.get("count",25)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(990100000+i,f"SPK-{i+1:03d}")
            _place(v,cl,cn,0.06); v.sog=float(cfg.get("sog_base",8))
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("base",v.sog); v.sx("spike",float(cfg.get("sog_spike",32)))
            v.sx("mode",cfg.get("mode","간헐")); v.sx("itv",float(cfg.get("interval",10)))
            v.sx("last_sp",0.0); v.sx("sp_on",False)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            if elapsed-v.x("last_sp")>=v.x("itv"):
                v.sx("last_sp",elapsed)
                if v.x("mode")=="간헐": v.sx("sp_on",not v.x("sp_on"))
                else: v.sx("sp_on",True)
            v.sog=v.x("spike") if v.x("sp_on") else v.x("base")
            if v.x("mode")=="순간" and v.x("sp_on"): v.sx("sp_on",False)
            if random.random()<0.15: v.cog=(v.cog+random.uniform(-20,20))%360
            v.hdg=int(v.cog); _step(v,dt)


# ─── A2  정박 이동 이상 ───────────────────────────
@_reg
class AnchorMove(AttackPlugin):
    meta = AttackMeta(
        key="anchor_move", label="A2  정박 이동 이상",
        category="A",
        purpose="navStatus=1 + SOG≥0.5 불일치 탐지",
        evasion="없음 — IDS Check3 검증용",
        expected_fn="정박 중 이동 즉시 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",1,300,30),
                _pi("반경 (도)","radius",0.01,1.0,0.10,0.01),
                _pi("이상 속도 (kn)","sog",0.1,10,3.0,0.1),
                _pi("COG (도)","cog",0,359,90,5)]

    def make(self,cfg):
        n=int(cfg.get("count",30)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(990500000+i,f"ANC-{i+1:03d}",nav=1)
            _place(v,cl,cn,float(cfg.get("radius",0.1)))
            v.sog=float(cfg.get("sog",3)); v.cog=float(cfg.get("cog",90))
            v.hdg=int((v.cog+120)%360)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            _step(v,dt); v.hdg=int((v.cog+120)%360)


# ─── A3  COG/HDG 불일치 ──────────────────────────
@_reg
class CourseMismatch(AttackPlugin):
    meta = AttackMeta(
        key="course_mismatch", label="A3  COG/HDG 불일치",
        category="A",
        purpose="COG-HDG diff>100° 탐지",
        evasion="없음 — IDS Check4 검증용",
        expected_fn="불일치 즉시 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",3,200,20),
                _pi("불일치 각도 (도)","mismatch",101,180,150,5),
                _pi("SOG (kn)","sog",0.5,30,10,0.5),
                _pi("COG 변화율","drift",0,20,5,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",20)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(990600000+i,f"CMM-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=float(cfg.get("sog",10))
            v.cog=random.uniform(0,360)
            v.hdg=int((v.cog+float(cfg.get("mismatch",150)))%360)
            v.sx("drift",float(cfg.get("drift",5)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        mm=float(cfg.get("mismatch",150))
        for v in fleet:
            if random.random()<0.1: v.cog=(v.cog+random.uniform(-v.x("drift"),v.x("drift")))%360
            v.hdg=int((v.cog+mm)%360); _step(v,dt)


# ─── A4  위치 점프 ────────────────────────────────
@_reg
class PositionJump(AttackPlugin):
    meta = AttackMeta(
        key="position_jump", label="A4  위치 점프",
        category="A",
        purpose="Haversine 거리 > 5km/min 탐지",
        evasion="없음 — IDS Check6 검증용",
        expected_fn="위치 점프 즉시 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",1,300,30),
                _pi("점프 반경 (도)","radius",0.05,2.0,0.3,0.05),
                _pi("점프 주기 (초)","interval",1,60,10,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",30)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(990700000+i,f"JMP-{i+1:03d}")
            _place(v,cl,cn,float(cfg.get("radius",0.3)))
            v.sog=random.uniform(2,10); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("r",float(cfg.get("radius",0.3))); v.sx("itv",float(cfg.get("interval",10)))
            v.sx("last_j",0.0)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            if elapsed-v.x("last_j")>=v.x("itv"):
                v.sx("last_j",elapsed)
                v.lat+=random.choice([-1,1])*random.uniform(0.08,0.2)
                v.lon+=random.choice([-1,1])*random.uniform(0.08,0.2)
                v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            _step(v,dt)


# ─── B1  고스트쉽 원형 정지 ──────────────────────
@_reg
class GhostCircleStatic(AttackPlugin):
    meta = AttackMeta(
        key="ghost_circle", label="B1  고스트쉽 원형 정지",
        category="B",
        purpose="갑작스러운 위치 출현 + navStatus=1",
        evasion="출현/소멸 반복으로 IDS 이력 초기화 유도",
        expected_fn="출현 시 위치 점프, 소멸 후 재출현은 신호 소실로 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",4,100,20),
                _pi("원 반경 (도)","radius",0.01,1.0,0.15,0.01),
                _pi("출현 주기 (초)","appear",5,300,30,5),
                _pi("유지 시간 (초)","vanish",5,300,20,5)]

    def make(self,cfg):
        n=int(cfg.get("count",20)); cl=cfg["clat"]; cn=cfg["clon"]
        r=float(cfg.get("radius",0.15))
        ap=float(cfg.get("appear",30)); va=float(cfg.get("vanish",20))
        fleet=[]
        for i in range(n):
            ang=2*math.pi*i/n
            v=Vessel(991000000+i,f"GHO-{i+1:02d}",nav=1)
            v.sx("clat",cl+math.cos(ang)*r); v.sx("clon",cn+math.sin(ang)*r*1.2)
            v.lat=0; v.lon=0; v.sog=0; v.cog=0; v.hdg=int(math.degrees(ang))
            v.sx("ap",ap); v.sx("va",va); v.sx("last",-(ap)); v.sx("vis",False)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            since=elapsed-v.x("last")
            if not v.x("vis"):
                if since>=v.x("ap"):
                    v.lat=v.x("clat")+random.uniform(-0.002,0.002)
                    v.lon=v.x("clon")+random.uniform(-0.002,0.002)
                    v.sx("last",elapsed); v.sx("vis",True)
            else:
                if since>=v.x("va"):
                    v.lat=0; v.lon=0; v.sx("vis",False); v.sx("last",elapsed)


# ─── B2  원형 순찰 (자연스러운 CTRV 기반) ────────
@_reg
class CirclePatrol(AttackPlugin):
    meta = AttackMeta(
        key="circle_patrol", label="B2  원형 순찰",
        category="B",
        purpose="원형 항적 패턴 + 선택적 속도 이상 주입",
        evasion="일정 각속도 유지 → CTRV 모델 잔차 낮음",
        expected_fn="속도 이상 주입 구간에서 SOG 초과 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",2,100,15),
                _pi("순찰 반경 (도)","radius",0.05,1.0,0.2,0.01),
                _pi("기본 SOG (kn)","sog",1,40,12,0.5),
                _pi("이상 SOG (kn, 0=없음)","spike_sog",0,60,0,1),
                _pi("이상 주기 (초)","spike_itv",5,120,20,5)]

    def make(self,cfg):
        n=int(cfg.get("count",15)); cl=cfg["clat"]; cn=cfg["clon"]
        r=float(cfg.get("radius",0.2)); sog=float(cfg.get("sog",12))
        fleet=[]
        for i in range(n):
            ang=2*math.pi*i/n
            v=Vessel(991050000+i,f"CPT-{i+1:02d}")
            v.lat=cl+math.cos(ang)*r; v.lon=cn+math.sin(ang)*r*1.2
            v.sog=sog+random.uniform(-0.5,0.5)
            v.cog=(math.degrees(ang)+90)%360; v.hdg=int(v.cog)
            v.sx("ang",ang); v.sx("r",r); v.sx("cl",cl); v.sx("cn",cn)
            v.sx("base_sog",v.sog); v.sx("spike",float(cfg.get("spike_sog",0)))
            v.sx("sitv",float(cfg.get("spike_itv",20)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        base_sog=float(cfg.get("sog",12)); spike=float(cfg.get("spike_sog",0))
        sitv=float(cfg.get("spike_itv",20))
        for v in fleet:
            circ=2*math.pi*v.x("r")
            omega=v.sog*_KN_TO_DPS/(circ+1e-9)
            v.sx("ang",(v.x("ang")+omega*dt)%(2*math.pi))
            v.lat=v.x("cl")+math.cos(v.x("ang"))*v.x("r")
            v.lon=v.x("cn")+math.sin(v.x("ang"))*v.x("r")*1.2
            v.cog=(math.degrees(v.x("ang"))+90)%360; v.hdg=int(v.cog)
            if spike>0 and (elapsed%sitv)/sitv<0.12:
                v.sog=spike
            else:
                v.sog=base_sog+random.uniform(-0.3,0.3)


# ─── B3  직선 왕복 (가속/감속 포함) ─────────────
@_reg
class LinearBounce(AttackPlugin):
    meta = AttackMeta(
        key="linear_bounce", label="B3  직선 왕복",
        category="B",
        purpose="직선 왕복 + 이상 속도 주입",
        evasion="반환점 근처 자연스러운 감속/가속 포함",
        expected_fn="이상 속도 구간 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",1,100,15),
                _pi("왕복 길이 (도)","length",0.1,2.0,0.4,0.05),
                _pi("방향 (도)","hdg",0,359,0,5),
                _pi("기본 SOG (kn)","sog",1,30,10,0.5),
                _pi("이상 SOG (kn, 0=없음)","spike_sog",0,60,0,1),
                _pi("이상 주기 (초)","spike_itv",5,120,20,5)]

    def make(self,cfg):
        n=int(cfg.get("count",15)); cl=cfg["clat"]; cn=cfg["clon"]
        L=float(cfg.get("length",0.4)); hdg=float(cfg.get("hdg",0))
        sog=float(cfg.get("sog",10)); rad=math.radians(hdg)
        fleet=[]
        for i in range(n):
            t=i/max(n-1,1)
            perp=math.radians(hdg+90); sp=(i-n//2)*0.003
            v=Vessel(991100000+i,f"LBN-{i+1:02d}")
            v.lat=cl+math.cos(rad)*L*(t-0.5)+math.cos(perp)*sp
            v.lon=cn+math.sin(rad)*L*(t-0.5)*1.2+math.sin(perp)*sp*1.2
            v.sog=sog+random.uniform(-0.5,0.5); v.cog=hdg; v.hdg=int(hdg)
            v.sx("s_lat",cl-math.cos(rad)*L/2)
            v.sx("s_lon",cn-math.sin(rad)*L/2*1.2)
            v.sx("e_lat",cl+math.cos(rad)*L/2)
            v.sx("e_lon",cn+math.sin(rad)*L/2*1.2)
            v.sx("fwd",t<0.5); v.sx("base_sog",v.sog)
            v.sx("spike",float(cfg.get("spike_sog",0)))
            v.sx("sitv",float(cfg.get("spike_itv",20)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        spike=float(cfg.get("spike_sog",0)); sitv=float(cfg.get("spike_itv",20))
        for v in fleet:
            # 반환점 근처 감속 (자연스러운 패턴)
            fwd=v.x("fwd")
            tx=v.x("e_lat") if fwd else v.x("s_lat")
            ty=v.x("e_lon") if fwd else v.x("s_lon")
            dist=math.sqrt((tx-v.lat)**2+(ty-v.lon)**2)+1e-9
            decel=min(1.0,dist/0.02)  # 반환점 0.02도 전부터 감속
            if spike>0 and (elapsed%sitv)/sitv<0.12:
                v.sog=spike
            else:
                v.sog=v.x("base_sog")*decel+0.3
            step=v.sog*_KN_TO_DPS*dt
            if dist<step*2: v.sx("fwd",not fwd)
            else:
                dl=tx-v.lat; dn=ty-v.lon
                v.lat+=dl/dist*step; v.lon+=dn/dist*step
            v.cog=math.degrees(math.atan2(ty-v.lat,tx-v.lon)+1e-9)%360
            v.hdg=int(v.cog)


# ─── B4  실제 항로 모방 + 이상 속도 ─────────────
_ROUTE_WPS=[
    (-0.20,0.00),(-0.13,0.07),(-0.05,0.14),(0.03,0.19),
    (0.10,0.17),(0.15,0.09),(0.18,0.00),(0.14,-0.10),
    (0.07,-0.17),(0.00,-0.20),(-0.10,-0.14),(-0.17,-0.07),(-0.20,0.00)
]

@_reg
class RealisticRoute(AttackPlugin):
    meta = AttackMeta(
        key="realistic_route", label="B4  실제루트 이상속도",
        category="B",
        purpose="정상 항로 이동 중 특정 WP에서 SOG 이상",
        evasion="13개 웨이포인트 실항로 → route conformance 통과",
        expected_fn="이상 SOG 구간에서 shipType maxSOG 초과 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("정상 SOG (kn)","sog_n",1,30,12,0.5),
                _pi("이상 SOG (kn)","sog_s",1,60,35,1),
                _pi("이상 WP 인덱스 (0~12)","spike_wp",0,12,4,1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        wps=[(cl+d[0],cn+d[1]*1.2) for d in _ROUTE_WPS]
        fleet=[]
        for i in range(n):
            si=int(i/n*len(wps))%len(wps)
            v=Vessel(991200000+i,f"RRT-{i+1:02d}")
            v.lat,v.lon=wps[si]
            v.lat+=random.uniform(-0.004,0.004); v.lon+=random.uniform(-0.004,0.004)
            v.sog=float(cfg.get("sog_n",12)); v.cog=0; v.hdg=0
            v.sx("wps",wps); v.sx("wi",si); v.sx("prog",0.0)
            v.sx("sog_n",float(cfg.get("sog_n",12)))
            v.sx("sog_s",float(cfg.get("sog_s",35)))
            v.sx("swp",int(cfg.get("spike_wp",4)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            wps=v.x("wps"); wi=v.x("wi"); nxt=(wi+1)%len(wps)
            cy,cx=wps[wi]; ny,nx=wps[nxt]
            d=math.sqrt((ny-cy)**2+(nx-cx)**2)+1e-9
            v.sog=v.x("sog_s") if wi==v.x("swp") else v.x("sog_n")
            v.sx("prog",v.x("prog")+v.sog*_KN_TO_DPS*dt/d)
            if v.x("prog")>=1.0:
                v.sx("prog",0.0); v.sx("wi",(wi+1)%len(wps))
                wi=(wi+1)%len(wps); nxt=(wi+1)%len(wps)
                cy,cx=wps[wi]; ny,nx=wps[nxt]; d=math.sqrt((ny-cy)**2+(nx-cx)**2)+1e-9
            p=v.x("prog")
            v.lat=cy+(ny-cy)*p; v.lon=cx+(nx-cx)*p
            v.cog=math.degrees(math.atan2(nx-cx,ny-cy)+1e-9)%360; v.hdg=int(v.cog)


# ─── B5  JBU 글자 선단 ───────────────────────────
_J=[( 0.08,0.04),(0.05,0.04),(0.02,0.04),(-0.01,0.04),(-0.04,0.03),(-0.06,0.01),(-0.06,-0.02)]
_B=[( 0.08,0.0),(0.04,0.0),(0.00,0.0),(-0.04,0.0),(-0.08,0.0),(-0.06,0.025),(-0.04,0.04),
    (-0.02,0.025),(0.0,0.0),(0.02,0.025),(0.04,0.04),(0.06,0.025),(0.08,0.0)]
_U=[( 0.08,0.0),(0.04,0.0),(0.00,0.0),(-0.04,0.005),(-0.07,0.02),(-0.07,0.05),
    (-0.04,0.06),(0.0,0.06),(0.04,0.05),(0.08,0.02)]

@_reg
class JBUFleet(AttackPlugin):
    meta = AttackMeta(
        key="jbu_fleet", label="B5  JBU 글자 선단",
        category="B",
        purpose="다수 선박 협조 이동으로 문자 형태 구성",
        evasion="각 선박의 개별 거동은 저속 정상 범위 내",
        expected_fn="fleet-level 협조 패턴 탐지 가능")

    def param_defs(self):
        return [_pi("글자 크기 배율","scale",0.5,5.0,1.0,0.1)]

    def make(self,cfg):
        cl=cfg["clat"]; cn=cfg["clon"]; sc=float(cfg.get("scale",1.0))
        fleet=[]
        def mk(pts,off_lat,off_lon,pfx,base_mmsi):
            for idx,(dl,dn) in enumerate(pts):
                v=Vessel(base_mmsi+idx,f"{pfx}{idx+1:02d}")
                v.lat=cl+off_lat+dl*sc; v.lon=cn+off_lon+dn*sc
                wps=[(cl+off_lat+p[0]*sc,cn+off_lon+p[1]*sc) for p in pts]
                v.sx("wps",wps); v.sx("bwps",list(wps))
                v.sx("wi",idx%len(pts)); v.sx("prog",0.0)
                v.sog=3.0+random.uniform(-0.3,0.3)
                fleet.append(v)
        mk(_J,-0.12*sc,-0.28*sc,"J-",990200000)
        mk(_B,-0.12*sc,-0.06*sc,"B-",990300000)
        mk(_U,-0.12*sc, 0.16*sc,"U-",990400000)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            wps=v.x("wps"); wi=v.x("wi"); nxt=(wi+1)%len(wps)
            cy,cx=wps[wi]; ny,nx=wps[nxt]
            d=math.sqrt((ny-cy)**2+(nx-cx)**2)+1e-9
            v.sx("prog",v.x("prog")+v.sog*_KN_TO_DPS*dt/d)
            if v.x("prog")>=1.0:
                v.sx("prog",0.0); v.sx("wi",nxt)
            p=v.x("prog")
            v.lat=cy+(ny-cy)*p; v.lon=cx+(nx-cx)*p
            v.cog=math.degrees(math.atan2(nx-cx,ny-cy)+1e-9)%360; v.hdg=int(v.cog)


# ─── B6  집게 협공 ────────────────────────────────
@_reg
class Pincer(AttackPlugin):
    meta = AttackMeta(
        key="pincer", label="B6  집게 협공",
        category="B",
        purpose="다수 선박 중심점 수렴 패턴",
        evasion="없음 — fleet 협조 패턴 탐지 검증",
        expected_fn="fleet-level 수렴 피처 탐지")

    def param_defs(self):
        return [_pi("선박 수 (양날 합)","count",4,80,20,2),
                _pi("날개 폭 (도)","width",0.05,2,0.5,0.05),
                _pi("종심 (도)","depth",0.05,1.5,0.3,0.05),
                _pi("수렴 속도 (kn)","speed",1,30,8,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",20)); cl=cfg["clat"]; cn=cfg["clon"]
        w=float(cfg.get("width",0.5)); dep=float(cfg.get("depth",0.3))
        half=n//2; fleet=[]
        for i in range(half):
            t=i/max(half-1,1)
            for side,sign in [("L",-1),("R",1)]:
                mmsi=990800000+(i if side=="L" else half+i)
                v=Vessel(mmsi,f"PNC-{side}{i+1:02d}")
                v.lat=cl+dep*(1-t); v.lon=cn+sign*w*t
                v.sx("tl",cl); v.sx("tn",cn)
                v.sog=float(cfg.get("speed",8))+random.uniform(-0.5,0.5)
                v.cog=math.degrees(math.atan2(cn-v.lon,cl-v.lat)+1e-9)%360
                v.hdg=int(v.cog)
                fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        spd=float(cfg.get("speed",8))
        for v in fleet:
            dl=v.x("tl")-v.lat; dn=v.x("tn")-v.lon
            dist=math.sqrt(dl**2+dn**2)+1e-9
            step=spd*_KN_TO_DPS*dt
            if dist>0.001: v.lat+=dl/dist*step; v.lon+=dn/dist*step
            v.cog=math.degrees(math.atan2(dn,dl)+1e-9)%360; v.hdg=int(v.cog); v.sog=spd


# ─── B7  파상 대형 ────────────────────────────────
@_reg
class Wave(AttackPlugin):
    meta = AttackMeta(
        key="wave", label="B7  파상 대형",
        category="B",
        purpose="사인파 횡진 + 전진 대형",
        evasion="각 선박의 COG 변화는 자연스러운 파도 회피 패턴처럼 보임",
        expected_fn="fleet-level 정렬 패턴 탐지 가능")

    def param_defs(self):
        return [_pi("선박 수","count",3,60,24,3),
                _pi("열 수","lanes",1,6,3,1),
                _pi("전체 폭 (도)","width",0.1,2,0.6,0.1),
                _pi("횡진폭 (도)","amp",0.01,0.5,0.15,0.01),
                _pi("전진 속도 (kn)","speed",1,30,10,0.5),
                _pi("사인 주파수","freq",0.005,0.2,0.05,0.005)]

    def make(self,cfg):
        n=int(cfg.get("count",24)); lanes=int(cfg.get("lanes",3))
        cl=cfg["clat"]; cn=cfg["clon"]
        w=float(cfg.get("width",0.6)); amp=float(cfg.get("amp",0.15))
        per=n//max(lanes,1); fleet=[]
        for lane in range(lanes):
            bn=cn+(lane-lanes/2)*(w/lanes)
            for i in range(per):
                idx=lane*per+i; t=i/max(per-1,1)
                v=Vessel(990900000+idx,f"WVE-{idx+1:02d}")
                v.lat=cl-amp*2*t; v.lon=bn
                v.sx("phase",(i/per)*2*math.pi+lane*math.pi/lanes)
                v.sx("bn",bn); v.sx("amp",amp)
                v.sog=float(cfg.get("speed",10)); v.cog=180; v.hdg=180
                fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        spd=float(cfg.get("speed",10)); freq=float(cfg.get("freq",0.05))
        for v in fleet:
            step=spd*_KN_TO_DPS*0.1; v.lat-=step
            lo=v.x("amp")*math.sin(v.x("phase")+elapsed*freq)
            v.lon=v.x("bn")+lo
            prev=v.x("amp")*math.sin(v.x("phase")+(elapsed-0.1)*freq)
            v.cog=(180+math.degrees(math.atan2(lo-prev,-step*111000)+1e-9))%360
            v.hdg=int(v.cog)



# ═══════════════════════════════════════════════════
#  C  규칙 IDS False-Negative 테스트
# ═══════════════════════════════════════════════════

# ─── C1  dt 구간 점프 ─────────────────────────────
@_reg
class BlindDtJump(AttackPlugin):
    meta = AttackMeta(
        key="blind_dt_jump", label="C1  [FN] dt 구간 점프",
        category="C",
        purpose="IDS Check5/6 회피: dt>60s이면 검사 skip",
        evasion="송신 주기를 65~120s로 설정 → dt 항상 >60",
        expected_fn="Check5·6 모두 skip, Check7(dt>300)도 아님 → 탐지 없음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("점프 거리 (도)","jdist",0.05,0.5,0.15,0.01)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(991700000+i,f"FN1-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=random.uniform(5,12)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("jd",float(cfg.get("jdist",0.15)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            d=v.x("jd"); ang=random.uniform(0,360)
            v.lat+=math.cos(math.radians(ang))*d
            v.lon+=math.sin(math.radians(ang))*d*1.2
            v.sog=random.choice([2.0,18.0,3.0,20.0])
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)


# ─── C2  속도 단계 상승 ───────────────────────────
@_reg
class BlindSpeedRamp(AttackPlugin):
    meta = AttackMeta(
        key="blind_speed_ramp", label="C2  [FN] 속도 단계 상승",
        category="C",
        purpose="IDS Check5 회피: Δsog < 10.0/메시지 유지",
        evasion="매 메시지 9.5kn씩 증가 → 임계값(10.0) 미달",
        expected_fn="2→11→20→29kn 달성해도 각 Δ<10 → 탐지 없음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("시작 SOG (kn)","start",0,5,2,0.5),
                _pi("증가량 (kn, <10)","step",1,9.9,9.5,0.1),
                _pi("상한 SOG (kn, <30)","maxs",5,29.9,29,1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(991800000+i,f"FN2-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=float(cfg.get("start",2))
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("step",float(cfg.get("step",9.5)))
            v.sx("maxs",float(cfg.get("maxs",29)))
            v.sx("start",float(cfg.get("start",2)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sog=min(v.sog+v.x("step"),v.x("maxs"))
            if v.sog>=v.x("maxs"): v.sog=v.x("start")
            v.hdg=int(v.cog); _step(v,dt)


# ─── C3  COG/HDG 경계값 ──────────────────────────
@_reg
class BlindCogBorder(AttackPlugin):
    meta = AttackMeta(
        key="blind_cog_border", label="C3  [FN] COG/HDG 경계값",
        category="C",
        purpose="IDS Check4 임계값(100°) 하회",
        evasion="91~99° 불일치 유지 → diff<100 → 탐지 없음",
        expected_fn="실제로는 비정상이나 임계가 느슨해 탐지 안됨")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("COG-HDG 불일치 (도)","mm",80,99.9,95,1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        mm=float(cfg.get("mm",95))
        fleet=[]
        for i in range(n):
            v=Vessel(991900000+i,f"FN3-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=random.uniform(3,10)
            v.cog=random.uniform(0,360); v.hdg=int((v.cog+mm)%360)
            v.sx("mm",mm)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            if random.random()<0.1: v.cog=(v.cog+random.uniform(-10,10))%360
            v.hdg=int((v.cog+v.x("mm"))%360); _step(v,dt)


# ─── C4  navStatus 회피 ───────────────────────────
@_reg
class BlindNavStatus(AttackPlugin):
    meta = AttackMeta(
        key="blind_nav_status", label="C4  [FN] navStatus 회피",
        category="C",
        purpose="IDS Check3 미검사 navStatus 악용",
        evasion="navStatus 2/3/7/8/11/12 에서 SOG≥0.5 이동",
        expected_fn="IDS는 1/5/6만 검사 → 나머지 상태는 탐지 없음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,12),
                _pi("SOG (kn, ≥0.5)","sog",0.5,20,3,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",12)); cl=cfg["clat"]; cn=cfg["clon"]
        stlist=[2,3,7,8,11,12]
        fleet=[]
        for i in range(n):
            v=Vessel(992000000+i,f"FN4-{i+1:03d}",nav=stlist[i%len(stlist)])
            _place(v,cl,cn,0.05); v.sog=float(cfg.get("sog",3))
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            if random.random()<0.05: v.cog=(v.cog+random.uniform(-15,15))%360
            v.hdg=int(v.cog); _step(v,dt)


# ═══════════════════════════════════════════════════
#  D  ML 우회 v1 (단일 피처 조작)
# ═══════════════════════════════════════════════════

# ─── D1  Low & Slow ───────────────────────────────
@_reg
class MLLowSlow(AttackPlugin):
    meta = AttackMeta(
        key="ml_low_slow", label="D1  [ML] Low & Slow",
        category="D",
        purpose="모든 규칙 임계값 동시 하회",
        evasion="Δsog<9.9, dist<5km/min, COG-HDG<99° 동시 유지",
        expected_fn="단일 피처는 정상이나 동시 조합이 ML에서 이상 패턴으로 포착될 수 있음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(991300000+i,f"LS-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=random.uniform(0.3,2)
            v.cog=random.uniform(0,360); v.hdg=int((v.cog+random.uniform(0,99))%360)
            v.sx("bc",v.cog); v.sx("bs",v.sog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sog=max(0.1,v.x("bs")+random.uniform(-0.15,0.15))
            v.cog=(v.x("bc")+random.uniform(-2,2))%360
            v.hdg=int((v.cog+random.uniform(50,95))%360)
            _step(v,dt)


# ─── D2  Temporal Camouflage ─────────────────────
@_reg
class MLTemporal(AttackPlugin):
    meta = AttackMeta(
        key="ml_temporal", label="D2  [ML] Temporal Camouflage",
        category="D",
        purpose="정상 N개 사이 이상 1개 삽입",
        evasion="window 피처 평균에 희석됨",
        expected_fn="window 크기에 따라 이상 score가 1/N 수준으로 희석")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("정상 메시지 수 N","norm_n",2,20,8,1),
                _pi("이상 SOG (kn)","anom_sog",10,60,40,1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(991400000+i,f"TC-{i+1:03d}")
            _place(v,cl,cn,0.05); v.sog=random.uniform(5,12)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("cnt",0); v.sx("nn",int(cfg.get("norm_n",8)))
            v.sx("as",float(cfg.get("anom_sog",40)))
            v.sx("bs",v.sog); v.sx("bc",v.cog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("cnt",v.x("cnt")+1)
            if v.x("cnt")%(v.x("nn")+1)==0:
                v.sog=v.x("as")
                v.cog=(v.x("bc")+175)%360; v.hdg=int((v.cog+160)%360)
            else:
                v.sog=v.x("bs")+random.uniform(-0.5,0.5)
                v.cog=(v.x("bc")+random.uniform(-5,5))%360; v.hdg=int(v.cog)
            _step(v,dt)


# ─── D3  Gradual Drift ────────────────────────────
@_reg
class MLGradualDrift(AttackPlugin):
    meta = AttackMeta(
        key="ml_gradual_drift", label="D3  [ML] Gradual Drift",
        category="D",
        purpose="GPS 노이즈 수준 이동 누적",
        evasion="각 스텝 ~44m (GPS 오차 범위), 누적 시 수십km",
        expected_fn="단기 window: 정상 / 장기 누적 피처 없으면 탐지 못함")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("스텝 크기 (도/틱)","step",0.0001,0.001,0.0004,0.0001)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(991500000+i,f"GD-{i+1:03d}",nav=1)
            _place(v,cl,cn,0.05); v.sog=random.uniform(0,0.3)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("dir",random.uniform(0,360))
            v.sx("step",float(cfg.get("step",0.0004)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            r=math.radians(v.x("dir")); st=v.x("step")
            v.lat+=math.cos(r)*st+random.uniform(-st*0.3,st*0.3)
            v.lon+=math.sin(r)*st*1.2+random.uniform(-st*0.3,st*0.3)
            v.sog=random.uniform(0,0.3)


# ─── D4  Feature Mimicry ─────────────────────────
@_reg
class MLMimicry(AttackPlugin):
    meta = AttackMeta(
        key="ml_mimicry", label="D4  [ML] Feature Mimicry",
        category="D",
        purpose="정상 SOG 프로파일 복사 + 실제 위치 다른 방향 이동",
        evasion="보고 SOG는 정상 분포 내, 실제 위치는 hidden 속도로 이동",
        expected_fn="개별 피처 정상 → fleet-level trajectory 분석 없으면 탐지 못함")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("실제 이동 속도 (kn)","hidden",1,40,15,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        prof=[8.0,8.2,8.5,8.3,8.1,7.9,8.0,8.2,8.4,8.3,8.1,8.0,7.8,8.0]
        fleet=[]
        for i in range(n):
            v=Vessel(991600000+i,f"MM-{i+1:03d}")
            _place(v,cl,cn,0.1); v.sog=prof[0]; v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("prof",prof); v.sx("pi",i%len(prof))
            v.sx("hdir",random.uniform(0,360))
            v.sx("hspd",float(cfg.get("hidden",15)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("pi",(v.x("pi")+1)%len(v.x("prof")))
            v.sog=v.x("prof")[v.x("pi")]
            v.cog=(v.cog+random.uniform(-3,3))%360; v.hdg=int(v.cog)
            step=v.x("hspd")*_KN_TO_DPS*dt
            v.lat+=math.cos(math.radians(v.x("hdir")))*step
            v.lon+=math.sin(math.radians(v.x("hdir")))*step*1.2



# ═══════════════════════════════════════════════════
#  E  ML 우회 v2 (구조적)
# ═══════════════════════════════════════════════════

# ─── E1  Smooth Trajectory (CTRV 기반) ───────────
@_reg
class AdvSmooth(AttackPlugin):
    meta = AttackMeta(
        key="adv_smooth", label="E1  [ADV] Smooth Trajectory",
        category="E",
        purpose="Kalman 잔차·jerk 피처 무력화",
        evasion="CTRV(등각속도·등속) 운동 유지 → 운동 모델 예측 오차 ≈ 0",
        expected_fn="residual-based scorer 통과. behavioral context만으로 탐지 가능")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("SOG (kn)","sog",1,40,15,0.5),
                _pi("선회율 (deg/s)","omega",0.1,10,2,0.1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993100000+i,f"SMT-{i+1:03d}")
            _place(v,cl,cn,0.08); v.sog=float(cfg.get("sog",15))+random.uniform(-1,1)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("om",float(cfg.get("omega",2))*random.choice([-1,1]))
            v.sx("bs",v.sog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.cog=(v.cog+v.x("om")*dt)%360; v.sog=v.x("bs"); v.hdg=int(v.cog)
            _step(v,dt)


# ─── E2  Fleet Desync ─────────────────────────────
@_reg
class AdvDesync(AttackPlugin):
    meta = AttackMeta(
        key="adv_desync", label="E2  [ADV] Fleet Desync",
        category="E",
        purpose="fleet-level 상관 피처 파괴",
        evasion="MMSI·shipType·SOG분포 개별화, 이상 발생 시각 분산",
        expected_fn="MMSI clustering·fleet variance 피처 무력화. 개별 탐지만 가능")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,12),
                _pi("이상 SOG (kn)","spike",10,60,38,1)]

    def make(self,cfg):
        n=int(cfg.get("count",12)); cl=cfg["clat"]; cn=cfg["clon"]
        used=set(); fleet=[]
        for i in range(n):
            mid=random.choice(_MID_POOL)
            while True:
                mmsi=mid*1000000+random.randint(100000,999999)
                if mmsi not in used: used.add(mmsi); break
            pr=random.choice(_PROFILES)
            v=Vessel(mmsi,f"{pr.name_pfx}{mmsi%10000:04d}")
            _place(v,cl,cn,0.1); v.sog=random.uniform(pr.sog_lo,pr.sog_hi)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog+random.uniform(-5,5))%360
            v.nav=random.choice(pr.nav_opts)
            v.sx("ao",random.uniform(0,45)); v.sx("ai",random.uniform(30,90))
            v.sx("bs",v.sog); v.sx("sp",float(cfg.get("spike",38))*random.uniform(0.85,1.15))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            adj=elapsed+v.x("ao"); cyc=adj%v.x("ai")
            v.sog=v.x("sp") if cyc<v.x("ai")*0.12 else v.x("bs")+random.uniform(-0.3,0.3)
            if random.random()<0.08: v.cog=(v.cog+random.uniform(-8,8))%360
            v.hdg=int(v.cog+random.uniform(-4,4))%360
            _step(v,dt)


# ─── E3  Window Edge ──────────────────────────────
@_reg
class AdvWindowEdge(AttackPlugin):
    meta = AttackMeta(
        key="adv_window_edge", label="E3  [ADV] Window Edge",
        category="E",
        purpose="sliding window anomaly score 희석",
        evasion="window_size-1 틱마다 이상 1회 → 양쪽 window에 각 1개만 포함",
        expected_fn="window당 anomaly score = 1/window_size → 임계 미달 가능성 높음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("ML window 크기 가정","wsize",5,50,20,1),
                _pi("이상 SOG (kn)","anom",10,60,42,1),
                _pi("정상 SOG (kn)","norm",1,30,12,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993300000+i,f"WED-{i+1:03d}")
            _place(v,cl,cn,0.06); v.sog=float(cfg.get("norm",12))
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("ws",int(cfg.get("wsize",20))); v.sx("tick",0)
            v.sx("an",float(cfg.get("anom",42))); v.sx("nm",float(cfg.get("norm",12)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("tick",v.x("tick")+1)
            cyc=v.x("tick")%v.x("ws")
            if cyc==v.x("ws")-1:
                v.sog=v.x("an"); v.cog=(v.cog+175+random.uniform(-3,3))%360
            else:
                v.sog=v.x("nm")+random.uniform(-0.4,0.4)
                if cyc==0: v.cog=(v.cog+175+random.uniform(-3,3))%360
            v.hdg=int(v.cog); _step(v,dt)


# ─── E4  Contextual Blend (어선 위장) ────────────
_FISH_PHASES=[
    ("net_out",   5.0,120,0.0,  0),
    ("trawling",  3.5,180,12.0, 7),
    ("hauling",   1.5, 90,0.0,  7),
    ("transit",  10.0, 60,0.0,  0),
    ("drifting",  0.4,150,4.0,  7),
]

@_reg
class AdvContextual(AttackPlugin):
    meta = AttackMeta(
        key="adv_contextual", label="E4  [ADV] Contextual Blend",
        category="E",
        purpose="ship-type 맥락 위장으로 ML 클러스터 오분류 유도",
        evasion="shipType=30+navStatus=7+조업 패턴 → 어선 클러스터로 평가됨",
        expected_fn="어선 전용 ML 모델 내에서 정상으로 처리될 가능성 높음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("실제 침투 방향 (도)","ddir",0,359,45,5),
                _pi("실제 침투 속도 (kn)","dsog",0.5,10,3,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            mmsi=random.choice([440,441])*1000000+random.randint(100000,999999)
            v=Vessel(mmsi,f"KFI{i+1:03d}")
            _place(v,cl,cn,0.08); v.sog=4; v.cog=random.uniform(0,360); v.hdg=int(v.cog); v.nav=7
            v.sx("pi",i%len(_FISH_PHASES)); v.sx("pe",random.uniform(0,_FISH_PHASES[i%len(_FISH_PHASES)][2]))
            v.sx("dd",float(cfg.get("ddir",45))+random.uniform(-10,10))
            v.sx("ds",float(cfg.get("dsog",3)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            ph=_FISH_PHASES[v.x("pi")]
            v.sx("pe",v.x("pe")+dt)
            if v.x("pe")>=ph[2]:
                v.sx("pe",0); v.sx("pi",(v.x("pi")+1)%len(_FISH_PHASES))
                ph=_FISH_PHASES[v.x("pi")]
            v.nav=ph[4]; v.sog=ph[1]+random.uniform(-0.3,0.3)
            if ph[3]>0: v.cog=(v.cog+ph[3]*dt*0.5)%360
            v.hdg=int(v.cog+random.uniform(-5,5))%360
            step=v.x("ds")*_KN_TO_DPS*dt
            v.lat+=math.cos(math.radians(v.x("dd")))*step
            v.lon+=math.sin(math.radians(v.x("dd")))*step*1.2


# ─── E5  Shadow Vessel ────────────────────────────
@_reg
class AdvShadow(AttackPlugin):
    meta = AttackMeta(
        key="adv_shadow", label="E5  [ADV] Shadow Vessel",
        category="E",
        purpose="MMSI 지역 정합·항로 준수로 규칙+ML 동시 우회",
        evasion="한국 MID(440/441)+연안화물 프로파일+실제 항로각 → 전 검사 통과",
        expected_fn="규칙 IDS: MMSI/SOG/navStatus 전 정상. ML: 정상 화물선 클러스터 내 위치")

    def param_defs(self):
        return [_pi("선박 수","count",1,30,8),
                _pi("접근 속도 (kn)","sog",5,20,12,0.5),
                _pi("목표 위도 오프셋","tlat_off",  -0.5, 0.5, 0.0, 0.05),
                _pi("목표 경도 오프셋","tlon_off", -0.5, 0.5, 0.0, 0.05)]

    def make(self,cfg):
        n=int(cfg.get("count",8)); cl=cfg["clat"]; cn=cfg["clon"]
        tl=cl+float(cfg.get("tlat_off",0)); tn=cn+float(cfg.get("tlon_off",0))
        fleet=[]
        for i in range(n):
            ang=2*math.pi*i/n; sd=0.35
            mmsi=random.choice([440,441])*1000000+random.randint(100000,999999)
            v=Vessel(mmsi,f"KR{mmsi%100000:05d}")
            v.lat=tl+math.cos(ang)*sd; v.lon=tn+math.sin(ang)*sd*1.2
            to_t=math.degrees(math.atan2(tn-v.lon,tl-v.lat)+1e-9)%360
            v.sog=float(cfg.get("sog",12))+random.uniform(-1.5,1.5)
            v.cog=to_t+random.uniform(-8,8); v.hdg=int(v.cog+random.uniform(-3,3))%360
            v.sx("tl",tl); v.sx("tn",tn); v.sx("bs",v.sog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            dl=v.x("tl")-v.lat; dn=v.x("tn")-v.lon
            dist=math.sqrt(dl**2+dn**2)+1e-9
            if dist<0.005:
                v.sog=random.uniform(0,0.4); v.nav=random.choice([0,5])
                v.lat+=random.uniform(-0.001,0.001); v.lon+=random.uniform(-0.001,0.001)
                return
            tc=math.degrees(math.atan2(dn,dl)+1e-9)%360
            v.cog=(0.85*v.cog+0.15*tc)%360
            v.hdg=int(v.cog+random.uniform(-3,3))%360
            v.sog=v.x("bs")+random.uniform(-0.5,0.5)
            _step(v,dt)



# ═══════════════════════════════════════════════════
#  F  고급 공격 (LSTM·clustering·hybrid·gap)
# ═══════════════════════════════════════════════════

# ─── F1  Feature Smoothing ────────────────────────
# 목적: ML feature의 Δ값을 학습된 정상 분포 내로 클램핑하면서 이동
# 동작: 매 틱 Δsog·Δcog·Δpos 전부를 "정상 분포 내 최대값"으로 제한
# 회피: 어떤 단일 피처 변화도 임계를 넘지 않음 → 누적 이동만 이상
@_reg
class FSmoothing(AttackPlugin):
    meta = AttackMeta(
        key="feat_smooth", label="F1  [F] Feature Smoothing",
        category="F",
        purpose="Δsog·Δcog·Δpos 전부 정상 분포 상위값으로 클램핑",
        evasion="어떤 단일 Δ피처도 임계 미달. 누적 변위만 이상",
        expected_fn="단기 window IDS 전 통과. 궤적 이탈 감지기만 탐지 가능")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("목표 위도 오프셋","tlat",  -0.5,0.5,0.2,0.01),
                _pi("목표 경도 오프셋","tlon", -0.5,0.5,0.2,0.01),
                _pi("max Δsog/틱 (kn)","dsog_max",0.1,9.9,3.0,0.1),
                _pi("max Δcog/틱 (도)","dcog_max",0.1,30,5.0,0.5),
                _pi("max Δpos/틱 (도)","dpos_max",0.001,0.04,0.01,0.001)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        tl=cl+float(cfg.get("tlat",0.2)); tn=cn+float(cfg.get("tlon",0.2))
        fleet=[]
        for i in range(n):
            v=Vessel(993500000+i,f"FSM-{i+1:03d}")
            _place(v,cl,cn,0.06)
            v.sog=random.uniform(5,12); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("tl",tl+random.uniform(-0.02,0.02))
            v.sx("tn",tn+random.uniform(-0.02,0.02))
            v.sx("dsmax",float(cfg.get("dsog_max",3)))
            v.sx("dcmax",float(cfg.get("dcog_max",5)))
            v.sx("dpmax",float(cfg.get("dpos_max",0.01)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            # 원하는 방향 계산
            dl=v.x("tl")-v.lat; dn=v.x("tn")-v.lon
            dist=math.sqrt(dl**2+dn**2)+1e-9
            want_cog=math.degrees(math.atan2(dn,dl)+1e-9)%360
            want_sog=min(15.0,dist/(_KN_TO_DPS*max(dt,0.1)))

            # Δ클램핑: 각 피처 변화를 정상 범위 내로 제한
            dcog=want_cog-v.cog
            if dcog>180: dcog-=360
            elif dcog<-180: dcog+=360
            dcog=max(-v.x("dcmax"),min(v.x("dcmax"),dcog))
            v.cog=(v.cog+dcog)%360

            dsog=want_sog-v.sog
            dsog=max(-v.x("dsmax"),min(v.x("dsmax"),dsog))
            v.sog=max(0,v.sog+dsog)

            v.hdg=int(v.cog)
            step=v.sog*_KN_TO_DPS*dt
            step=min(step,v.x("dpmax"))   # 위치 변화도 클램핑
            v.lat+=math.cos(math.radians(v.cog))*step
            v.lon+=math.sin(math.radians(v.cog))*step*1.2


# ─── F2  Intermittent Spoofing ────────────────────
# 목적: 간헐적 위조 — 정상 구간과 이상 구간을 교번
# 동작: T_normal초 동안 완전 정상 거동, T_attack초 동안 이상 삽입
# 회피: 이상 구간이 전체의 T_attack/(T_n+T_a) 비율만 차지
@_reg
class IntermittentSpoof(AttackPlugin):
    meta = AttackMeta(
        key="intermittent", label="F2  [F] Intermittent Spoofing",
        category="F",
        purpose="정상/이상 교번으로 anomaly score 시간 평균 희석",
        evasion="전체 시간의 일부만 이상 → 평균 score = α × peak_score",
        expected_fn="T_normal이 클수록 평균 score 낮음. 순간 탐지기만 잡음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("정상 구간 (초)","tn",5,120,30,5),
                _pi("이상 구간 (초)","ta",1,60,5,1),
                _pi("이상 SOG (kn)","anom",10,60,45,1),
                _pi("이상 navStatus","anav",0,15,6,1)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993600000+i,f"ISP-{i+1:03d}")
            _place(v,cl,cn,0.06); v.sog=random.uniform(5,12)
            v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("tn",float(cfg.get("tn",30))); v.sx("ta",float(cfg.get("ta",5)))
            v.sx("anom",float(cfg.get("anom",45))); v.sx("anav",int(cfg.get("anav",6)))
            v.sx("bs",v.sog); v.sx("phase_off",random.uniform(0,float(cfg.get("tn",30))))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            tn=v.x("tn"); ta=v.x("ta"); cycle=tn+ta
            t=(elapsed+v.x("phase_off"))%cycle
            if t<ta:
                # 이상 구간
                v.sog=v.x("anom"); v.nav=v.x("anav")
                v.cog=(v.cog+random.uniform(-60,60))%360
            else:
                # 정상 구간
                v.sog=v.x("bs")+random.uniform(-0.5,0.5); v.nav=0
                v.cog=(v.cog+random.uniform(-3,3))%360
            v.hdg=int(v.cog); _step(v,dt)


# ─── F3  Trajectory Stitching ─────────────────────
# 목적: 두 정상 궤적 사이를 3차 보간으로 봉합
# 동작: Phase A(정상 항해) → Hermite spline 전환 → Phase B(목표지 항해)
# 회피: 전환 구간이 수학적으로 C1 연속 → jerk/curvature 피처 낮음
@_reg
class TrajStitch(AttackPlugin):
    meta = AttackMeta(
        key="traj_stitch", label="F3  [F] Trajectory Stitching",
        category="F",
        purpose="trajectory continuity 피처 무력화",
        evasion="C1 연속 Hermite spline으로 궤적 봉합 → 곡률·jerk 자연스러움",
        expected_fn="단순 속도 기반 탐지기 통과. high-order curvature 분석만 잡음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("Phase A 방향 (도)","hdg_a",0,359,90,5),
                _pi("Phase B 방향 (도)","hdg_b",0,359,270,5),
                _pi("전환 시간 (초)","stitch_t",5,60,20,5),
                _pi("SOG (kn)","sog",1,30,12,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993700000+i,f"STC-{i+1:03d}")
            _place(v,cl,cn,0.06)
            v.sog=float(cfg.get("sog",12))+random.uniform(-1,1)
            v.cog=float(cfg.get("hdg_a",90)); v.hdg=int(v.cog)
            v.sx("ha",float(cfg.get("hdg_a",90)))
            v.sx("hb",float(cfg.get("hdg_b",270)))
            v.sx("st",float(cfg.get("stitch_t",20)))
            v.sx("bs",v.sog); v.sx("phase","A")
            v.sx("pt",0.0)  # phase 경과 시간
            v.sx("stitch_start",random.uniform(10,30))  # A 구간 길이
            v.sx("stitch_done",False)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("pt",v.x("pt")+dt)
            phase=v.x("phase")
            if phase=="A":
                v.cog=v.x("ha")+random.uniform(-2,2)
                v.sog=v.x("bs")+random.uniform(-0.3,0.3)
                if v.x("pt")>=v.x("stitch_start"):
                    v.sx("phase","S"); v.sx("pt",0)
                    v.sx("s_lat",v.lat); v.sx("s_lon",v.lon)
                    v.sx("s_cog",v.cog)
            elif phase=="S":
                # Hermite 보간: t 0→1
                t=min(1.0,v.x("pt")/v.x("st"))
                h00=2*t**3-3*t**2+1; h10=t**3-2*t**2+t
                h01=-2*t**3+3*t**2; h11=t**3-t**2
                # tangent: A방향/B방향 단위벡터 × 전환거리
                td=v.sog*_KN_TO_DPS*v.x("st")
                ra=math.radians(v.x("ha")); rb=math.radians(v.x("hb"))
                m0y=math.cos(ra)*td; m0x=math.sin(ra)*td
                m1y=math.cos(rb)*td; m1x=math.sin(rb)*td
                p0y=v.x("s_lat"); p0x=v.x("s_lon")
                p1y=p0y+math.cos(rb)*td; p1x=p0x+math.sin(rb)*td
                v.lat=h00*p0y+h10*m0y+h01*p1y+h11*m1y
                v.lon=h00*p0x+h10*m0x+h01*p1x+h11*m1x
                # COG = spline tangent 방향
                dh00=6*t**2-6*t; dh10=3*t**2-4*t+1
                dh01=-6*t**2+6*t; dh11=3*t**2-2*t
                dcog_y=dh00*p0y+dh10*m0y+dh01*p1y+dh11*m1y
                dcog_x=dh00*p0x+dh10*m0x+dh01*p1x+dh11*m1x
                v.cog=math.degrees(math.atan2(dcog_x,dcog_y)+1e-9)%360
                if t>=1.0: v.sx("phase","B"); v.sx("pt",0)
            else:  # Phase B
                v.cog=v.x("hb")+random.uniform(-2,2)
                v.sog=v.x("bs")+random.uniform(-0.3,0.3)
                _step(v,dt)
            v.hdg=int(v.cog)


# ─── F4  Time Skew ────────────────────────────────
# 목적: Δt 불규칙 조작으로 velocity 피처 왜곡
# 동작: 빠른 burst (짧은 Δt) + 긴 침묵 (긴 Δt) 교번
#       ML이 Δpos/Δt로 속도를 재구성하면 burst 구간은
#       실제 속도의 수배로 계산됨
# 회피: 각 메시지의 SOG 필드는 정상값 → 보고 vs 추정 불일치
@_reg
class TimeSkew(AttackPlugin):
    meta = AttackMeta(
        key="time_skew", label="F4  [F] Time Skew",
        category="F",
        purpose="Δt 조작으로 ML 재구성 속도와 보고 SOG 불일치 생성",
        evasion="보고 SOG는 정상 → 규칙 IDS 통과. ML은 Δpos/Δt로 이상 감지 어려움",
        expected_fn="SOG-필드 기반 탐지기 통과. Δpos/Δt 재구성 탐지기만 잡을 수 있음")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("burst 메시지 수","burst_n",2,10,4,1),
                _pi("burst 이동 거리 (도)","burst_dist",0.01,0.3,0.08,0.01),
                _pi("보고 SOG (kn, 정상값)","rep_sog",1,30,10,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993800000+i,f"TSK-{i+1:03d}")
            _place(v,cl,cn,0.06)
            v.sog=float(cfg.get("rep_sog",10)); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("bn",int(cfg.get("burst_n",4)))
            v.sx("bd",float(cfg.get("burst_dist",0.08)))
            v.sx("rs",float(cfg.get("rep_sog",10)))
            v.sx("tick",0); v.sx("mode","idle")
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("tick",v.x("tick")+1)
            t=v.x("tick"); bn=v.x("bn")
            # burst: bn개 메시지를 연속으로 큰 이동
            if t%(bn*3)<bn:
                v.sx("mode","burst")
                # 한 틱에 burst_dist/bn 이동 (빠른 실제 이동)
                step=v.x("bd")/bn
                v.lat+=math.cos(math.radians(v.cog))*step
                v.lon+=math.sin(math.radians(v.cog))*step*1.2
            else:
                v.sx("mode","idle")
                # idle: 거의 움직이지 않음 (하지만 SOG는 정상 보고)
                _step(v,dt*0.05)
            # 보고 SOG는 항상 정상값
            v.sog=v.x("rs")+random.uniform(-0.3,0.3)
            v.hdg=int(v.cog)


# ─── F5  Multi-Ship Coordination ─────────────────
# 목적: 선박군이 협조적으로 동일 목표로 이동하되
#       개별 거동은 정상으로 보이게 함
# 동작: 각 선박이 목표 방향으로 조금씩 편향된 정상 항로를 따름
#       개별 분석: 완전 정상
#       fleet-level: 수렴 벡터 필드가 형성됨
@_reg
class MultiCoord(AttackPlugin):
    meta = AttackMeta(
        key="multi_coord", label="F5  [F] Multi-Ship Coordination",
        category="F",
        purpose="fleet-level 협조 탐지 vs 개별 정상성 트레이드오프",
        evasion="각 선박 개별 피처는 완전 정상. 協調는 fleet-level에만 나타남",
        expected_fn="개별 IDS 전 통과. fleet-level 수렴 벡터 분석만 탐지 가능")

    def param_defs(self):
        return [_pi("선박 수","count",3,50,12),
                _pi("목표 위도 오프셋","tlat",  -0.5,0.5,0.15,0.05),
                _pi("목표 경도 오프셋","tlon", -0.5,0.5,0.15,0.05),
                _pi("SOG (kn)","sog",5,20,10,0.5),
                _pi("목표 편향 가중치","bias",0.0,1.0,0.3,0.05)]

    def make(self,cfg):
        n=int(cfg.get("count",12)); cl=cfg["clat"]; cn=cfg["clon"]
        tl=cl+float(cfg.get("tlat",0.15)); tn=cn+float(cfg.get("tlon",0.15))
        fleet=[]
        for i in range(n):
            pr=random.choice(_PROFILES)
            mmsi=random.choice([440,441,477,413])*1000000+random.randint(100000,999999)
            v=Vessel(mmsi,f"{pr.name_pfx}{i+1:03d}")
            _place(v,cl,cn,0.15)
            v.sog=float(cfg.get("sog",10))+random.uniform(-1,1)
            # 각 선박의 기본 항로: 실제 항로 방향 중 가장 가까운 것
            tc=math.degrees(math.atan2(tn-v.lon,tl-v.lat)+1e-9)%360
            base=min(_COASTAL_HDGS,key=lambda h:min(abs(h-tc),360-abs(h-tc)))
            v.cog=base+random.uniform(-10,10); v.hdg=int(v.cog)
            v.sx("tl",tl); v.sx("tn",tn)
            v.sx("bias",float(cfg.get("bias",0.3)))
            v.sx("base_cog",v.cog); v.sx("bs",v.sog)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        bias=float(cfg.get("bias",0.3))
        for v in fleet:
            # 목표 방향
            dl=v.x("tl")-v.lat; dn=v.x("tn")-v.lon
            tc=math.degrees(math.atan2(dn,dl)+1e-9)%360
            # 기본 항로 + bias × 목표 방향 blend (자연스럽게 편향)
            v.cog=((1-bias)*v.x("base_cog")+bias*tc+random.uniform(-5,5))%360
            v.sog=v.x("bs")+random.uniform(-0.4,0.4)
            v.hdg=int(v.cog+random.uniform(-4,4))%360
            _step(v,dt)


# ─── F6  AIS Gap Attack ───────────────────────────
# 목적: 신호 끊김을 악용한 위치 도약
# 동작: T_silence초 동안 신호를 끊은 뒤 목표 위치 근처에서 재등장
#       IDS Check7(dt>300)은 "신호 소실" 경보를 내지만
#       재등장 위치 자체는 새 시작점으로 처리됨
# 회피: 재등장 후 최소 2개 이상의 정상 메시지를 보내면
#       이전 이력이 희석되거나 reset됨 (IDS 구현에 따라 다름)
@_reg
class AISGap(AttackPlugin):
    meta = AttackMeta(
        key="ais_gap", label="F6  [F] AIS Gap",
        category="F",
        purpose="신호 소실 → 재등장으로 IDS 이력 리셋 유도",
        evasion="T_silence>300s → Check7만 발생. 재등장 후 이력 재시작",
        expected_fn="재등장 위치가 이전 위치와 멀어도 이력 없으면 Check5/6 미적용")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,8),
                _pi("침묵 시간 (초)","silence",31,600,120,10),
                _pi("활동 시간 (초)","active",10,120,30,5),
                _pi("재등장 오프셋 (도)","jump",0.05,0.5,0.2,0.05),
                _pi("재등장 SOG (kn)","sog",1,30,10,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",8)); cl=cfg["clat"]; cn=cfg["clon"]
        fleet=[]
        for i in range(n):
            v=Vessel(993900000+i,f"GAP-{i+1:03d}")
            _place(v,cl,cn,0.08)
            v.sog=float(cfg.get("sog",10)); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("silence",float(cfg.get("silence",120)))
            v.sx("active",float(cfg.get("active",30)))
            v.sx("jump",float(cfg.get("jump",0.2)))
            v.sx("phase","active"); v.sx("pt",random.uniform(0,float(cfg.get("active",30))))
            v.sx("orig_lat",v.lat); v.sx("orig_lon",v.lon)
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            v.sx("pt",v.x("pt")+dt)
            if v.x("phase")=="active":
                v.sog=float(cfg.get("sog",10))+random.uniform(-0.5,0.5)
                v.cog=(v.cog+random.uniform(-3,3))%360; v.hdg=int(v.cog)
                _step(v,dt)
                if v.x("pt")>=v.x("active"):
                    v.sx("phase","silent"); v.sx("pt",0)
                    # 침묵 전 위치 저장 후 목표 재등장 위치 미리 계산
                    ang=random.uniform(0,360); jd=v.x("jump")
                    v.sx("next_lat",v.lat+math.cos(math.radians(ang))*jd)
                    v.sx("next_lon",v.lon+math.sin(math.radians(ang))*jd*1.2)
                    # 침묵: 위치를 0,0으로 (IDS에는 신호 소실로 처리)
                    v.lat=0; v.lon=0; v.sog=0
            else:  # silent
                if v.x("pt")>=v.x("silence"):
                    # 재등장: 새 위치에서 시작
                    v.lat=v.x("next_lat"); v.lon=v.x("next_lon")
                    v.sog=float(cfg.get("sog",10)); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
                    v.sx("phase","active"); v.sx("pt",0)


# ─── F7  LSTM Beat ────────────────────────────────
# 목적: LSTM hidden state를 정상 분포 내로 유지하는 궤적 생성
# 동작: ΔSOG·ΔCOG·Δpos 전부 정상 학습 데이터의 1σ 이내로 유지하면서
#       목표 위치 방향으로 아주 천천히 이동 (Gradual Drift의 고급버전)
#       ΔSOG~N(0,0.5), ΔCOG~N(0,3°), Δpos~U(0,0.002°) 가정
@_reg
class LSTMBeat(AttackPlugin):
    meta = AttackMeta(
        key="lstm_beat", label="F7  [F] LSTM Beat",
        category="F",
        purpose="LSTM hidden state를 정상 분포 내로 유지",
        evasion="모든 Δ피처를 정상 학습 1σ 이내로 클램핑. 누적 위치만 이상",
        expected_fn="LSTM reconstruction error ≈ 0. 누적 drift 감지기만 탐지")

    def param_defs(self):
        return [_pi("선박 수","count",1,50,10),
                _pi("목표 위도 오프셋","tlat",-0.5,0.5,0.3,0.01),
                _pi("목표 경도 오프셋","tlon",-0.5,0.5,0.3,0.01),
                _pi("σ_sog (정상 ΔSOG 표준편차)","s_sog",0.1,3.0,0.5,0.1),
                _pi("σ_cog (정상 ΔCOG 표준편차)","s_cog",0.5,10.0,3.0,0.5)]

    def make(self,cfg):
        n=int(cfg.get("count",10)); cl=cfg["clat"]; cn=cfg["clon"]
        tl=cl+float(cfg.get("tlat",0.3)); tn=cn+float(cfg.get("tlon",0.3))
        fleet=[]
        for i in range(n):
            v=Vessel(994000000+i,f"LBT-{i+1:03d}")
            _place(v,cl,cn,0.06)
            v.sog=random.uniform(6,12); v.cog=random.uniform(0,360); v.hdg=int(v.cog)
            v.sx("tl",tl+random.uniform(-0.02,0.02))
            v.sx("tn",tn+random.uniform(-0.02,0.02))
            v.sx("ss",float(cfg.get("s_sog",0.5)))
            v.sx("sc",float(cfg.get("s_cog",3.0)))
            fleet.append(v)
        return fleet

    def update(self,fleet,elapsed,dt,cfg):
        for v in fleet:
            # 원하는 방향 계산 (목표 쪽으로 아주 작은 편향)
            dl=v.x("tl")-v.lat; dn=v.x("tn")-v.lon
            dist=math.sqrt(dl**2+dn**2)+1e-9
            want_cog=math.degrees(math.atan2(dn,dl)+1e-9)%360
            # ΔCOG: 정상 1σ 내에서 목표 방향으로 조금씩 편향
            dcog=want_cog-v.cog
            if dcog>180: dcog-=360
            elif dcog<-180: dcog+=360
            # 한 번에 최대 1σ_cog만 이동
            dcog=max(-v.x("sc"),min(v.x("sc"),dcog*0.15))
            v.cog=(v.cog+dcog)%360
            # ΔSOG: 정상 N(0,σ_sog)
            dsog=random.gauss(0,v.x("ss"))*0.3
            v.sog=max(2.0,min(20.0,v.sog+dsog))
            v.hdg=int(v.cog)
            # Δpos: 최대 0.002도/틱 (GPS 정밀도 수준)
            step=min(v.sog*_KN_TO_DPS*dt,0.002)
            v.lat+=math.cos(math.radians(v.cog))*step
            v.lon+=math.sin(math.radians(v.cog))*step*1.2



# ═══════════════════════════════════════════════════
#  §6  SimEngine
# ═══════════════════════════════════════════════════
class SimEngine:
    """송신 루프 — 패턴·RT 오버라이드·UDP 전송을 캡슐화"""

    def __init__(self, cfg:dict, log_q:queue.Queue, stop:threading.Event):
        self.cfg=cfg; self.log_q=log_q; self.stop=stop

    # ── RT 오버라이드 적용 ─────────────────────────
    @staticmethod
    def _apply_rt(fleet:list[Vessel])->None:
        if not RT.active: return
        if RT.manual_jump:
            RT.manual_jump=False
            jd=RT.jump_dist
            for v in fleet:
                ang=random.uniform(0,360)
                v.lat+=math.cos(math.radians(ang))*jd
                v.lon+=math.sin(math.radians(ang))*jd*1.2
        for v in fleet:
            v.sog=max(0,v.sog*RT.sog_mult)
            v.cog=(v.cog+RT.cog_offset)%360
            v.hdg=(v.hdg+int(RT.cog_offset))%360
            if RT.pos_scatter>0:
                v.lat+=random.uniform(-RT.pos_scatter,RT.pos_scatter)
                v.lon+=random.uniform(-RT.pos_scatter,RT.pos_scatter)*1.2
            if RT.nav_override>=0: v.nav=RT.nav_override

    # ── 메인 루프 ──────────────────────────────────
    def run(self)->None:
        cfg=self.cfg
        host=cfg["host"]; port=int(cfg["port"])
        interval=float(cfg["interval"])
        key=cfg["attack_key"]
        plugin=AttackRegistry.get(key)
        fleet=plugin.make(cfg)
        name_sent:set[int]=set()
        iteration=0; start=time.time()
        _qlog(f"[SimEngine] {plugin.meta.label} | {len(fleet)}척 | {host}:{port}","start")
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            while not self.stop.is_set():
                iteration+=1; t0=time.time(); elapsed=t0-start
                plugin.update(fleet,elapsed,interval,cfg)
                self._apply_rt(fleet)
                sent=0
                for v in fleet:
                    if self.stop.is_set(): return
                    if v.mmsi not in name_sent:
                        sock.sendto(v.name_msg().encode("ascii"),(host,port))
                        name_sent.add(v.mmsi)
                        if not _sleep(self.stop,0.01): return
                    sock.sendto(v.pos_msg().encode("ascii"),(host,port))
                    sent+=1
                    if not _sleep(self.stop,0.004): return
                dt=time.time()-t0
                if iteration==1 or iteration%10==0:
                    rt_tag=" | RT" if RT.active else ""
                    _qlog(f"[{iteration}] {sent}건 {dt:.2f}s{rt_tag}","info")
                if not _sleep(self.stop,max(0,interval-dt)): return


def _file_loop(cfg:dict,log_q:queue.Queue,stop:threading.Event)->None:
    host=cfg["host"]; port=int(cfg["port"])
    msgs=load_nmea(cfg["file_path"])
    interval=float(cfg["file_interval"]); repeat=bool(cfg["file_repeat"])
    _qlog(f"[파일] {Path(cfg['file_path']).name} | {len(msgs)}개","start")
    cycle=0
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        while not stop.is_set():
            cycle+=1
            for i,msg in enumerate(msgs,1):
                if stop.is_set(): return
                sock.sendto(msg.encode("ascii"),(host,port))
                _qlog(f"[파일 {i:04d}] {msg.strip()}","info")
                if not _sleep(stop,interval): return
            if not repeat:
                _qlog(f"[파일 완료] 총 {cycle*len(msgs)}건","start"); return
            _qlog(f"[파일 반복] {cycle}회차","info")


def sender_worker(channel:str,cfg:dict,stop:threading.Event)->None:
    try:
        if channel=="generated":
            SimEngine(cfg,_LOG_Q,stop).run()
        else:
            _file_loop(cfg,_LOG_Q,stop)
    except Exception as e:
        _qlog(f"[오류] {e}","error")
    finally:
        lbl="생성" if channel=="generated" else "파일"
        _qlog(f"[{lbl}] {'중단' if stop.is_set() else '완료'}","start")
        _qstate(channel,"finished")


# ═══════════════════════════════════════════════════
#  §7  RealTimeControlWindow
# ═══════════════════════════════════════════════════
class RealTimeControlWindow(tk.Toplevel):
    BG="#09111d"; AC="#ff9f44"; FG="#edf4ff"; SB="#9db0c7"; EB="#172334"

    def __init__(self,master):
        super().__init__(master)
        self.title("🎮  RT Control  |  실시간 조작")
        self.configure(bg=self.BG); self.minsize(440,620); self.resizable(True,True)
        self.v_act=tk.BooleanVar(value=False)
        self.v_sm =tk.DoubleVar(value=1.0)
        self.v_co =tk.DoubleVar(value=0.0)
        self.v_ps =tk.DoubleVar(value=0.0)
        self.v_nav=tk.IntVar(value=-1)
        self.v_jd =tk.DoubleVar(value=0.05)
        for var in (self.v_act,self.v_sm,self.v_co,self.v_ps,self.v_nav,self.v_jd):
            var.trace_add("write",self._sync)
        self._build(); self._sync()

    def _lbl(self,p,t,sub=False):
        tk.Label(p,text=t,bg=self.BG,fg=self.SB if sub else self.FG,
                 font=("Consolas",9 if sub else 10)).pack(anchor="w",padx=16,pady=(4,0))

    def _slider(self,p,lbl,var,lo,hi,res=0.01,fmt="{:.2f}"):
        row=tk.Frame(p,bg=self.BG); row.pack(fill="x",padx=14,pady=3)
        tk.Label(row,text=lbl,bg=self.BG,fg=self.SB,
                 font=("Consolas",9),width=22,anchor="w").pack(side="left")
        vl=tk.Label(row,text=fmt.format(var.get()),bg=self.BG,fg=self.AC,
                    font=("Consolas",10,"bold"),width=8); vl.pack(side="right")
        def upd(*_): vl.config(text=fmt.format(var.get()))
        tk.Scale(row,variable=var,from_=lo,to=hi,resolution=res,orient="horizontal",
                 bg=self.BG,fg=self.FG,activebackground=self.AC,highlightthickness=0,
                 troughcolor=self.EB,sliderlength=18,
                 command=lambda _:upd()).pack(side="left",fill="x",expand=True,padx=(6,4))

    def _build(self):
        tk.Label(self,text="  REAL-TIME CONTROL  |  실시간 조작",
                 bg=self.AC,fg="#000",font=("Consolas",12,"bold"),pady=7).pack(fill="x")
        tk.Label(self,text="  현재 실행 중인 선단에 즉시 반영",
                 bg="#1a2030",fg=self.SB,font=("Consolas",8),pady=3).pack(fill="x")
        self.abtn=tk.Button(self,text="○ 비활성",bg="#24354d",fg=self.FG,
                            font=("Consolas",11,"bold"),relief="flat",cursor="hand2",
                            padx=12,pady=7,command=self._toggle)
        self.abtn.pack(fill="x",padx=14,pady=(12,4))
        tk.Frame(self,height=1,bg="#24354d").pack(fill="x",padx=10,pady=6)
        self._lbl(self,"SOG 배율  (1.0 = 원래 속도)")
        self._slider(self,"sog_mult",self.v_sm,0,5,0.05,"{:.2f}×")
        self._lbl(self,"COG 오프셋  (도, 전체 선단 회전)")
        self._slider(self,"cog_offset",self.v_co,-180,180,1,"{:+.0f}°")
        self._lbl(self,"위치 노이즈  (도)")
        self._slider(self,"scatter",self.v_ps,0,0.5,0.001,"{:.3f}°")
        self._lbl(self,"navStatus 강제  (-1=비활성)")
        self._slider(self,"nav_override",self.v_nav,-1,15,1,"{:.0f}")
        tk.Frame(self,height=1,bg="#24354d").pack(fill="x",padx=10,pady=6)
        self._lbl(self,"수동 위치 점프")
        self._slider(self,"jump_dist",self.v_jd,0.01,0.5,0.01,"{:.2f}°")
        self.jbtn=tk.Button(self,text="⚡ 즉시 점프",bg="#3d1a1a",fg="#ff6b6b",
                             font=("Consolas",11,"bold"),relief="flat",cursor="hand2",
                             pady=7,command=self._jump)
        self.jbtn.pack(fill="x",padx=14,pady=(4,6))
        tk.Frame(self,height=1,bg="#24354d").pack(fill="x",padx=10,pady=4)
        self.stlbl=tk.Label(self,text="● 비활성",bg=self.BG,fg="#556677",
                            font=("Consolas",9),pady=5); self.stlbl.pack(fill="x",padx=16)
        tk.Button(self,text="초기화",bg="#172334",fg=self.SB,
                  font=("Consolas",10),relief="flat",cursor="hand2",pady=4,
                  command=self._reset).pack(fill="x",padx=14,pady=(4,12))

    def _toggle(self):
        self.v_act.set(not self.v_act.get()); self._sync(); self._upd_btn()
    def _upd_btn(self):
        if self.v_act.get():
            self.abtn.config(text="● 활성화됨 — 클릭하여 비활성",bg="#1a3d1a",fg="#44ff88")
            self.stlbl.config(text="● 활성 — 다음 틱에 반영",fg="#44ff88")
        else:
            self.abtn.config(text="○ 비활성 — 클릭하여 활성화",bg="#24354d",fg=self.FG)
            self.stlbl.config(text="● 비활성",fg="#556677")
    def _sync(self,*_):
        RT.active=bool(self.v_act.get()); RT.sog_mult=float(self.v_sm.get())
        RT.cog_offset=float(self.v_co.get()); RT.pos_scatter=float(self.v_ps.get())
        RT.nav_override=int(self.v_nav.get()); RT.jump_dist=float(self.v_jd.get())
    def _jump(self):
        RT.manual_jump=True
        self.jbtn.config(bg="#6b1a1a"); self.after(300,lambda:self.jbtn.config(bg="#3d1a1a"))
    def _reset(self):
        self.v_act.set(False); self.v_sm.set(1.0); self.v_co.set(0.0)
        self.v_ps.set(0.0); self.v_nav.set(-1); self.v_jd.set(0.05)
        RT.manual_jump=False; self._upd_btn()



# ═══════════════════════════════════════════════════
#  §8  GUI — App
# ═══════════════════════════════════════════════════
_DEFAULT_FILE = Path(__file__).with_name("nmea_data_sample.txt")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenCPN AIS IDS Signal Generator  v6")
        self.configure(bg="#09111d"); self.minsize(1120,720); self.resizable(True,True)
        self._gen_thread: threading.Thread|None = None
        self._file_thread: threading.Thread|None = None
        self._gen_stop  = threading.Event()
        self._file_stop = threading.Event()
        self._rt_win: RealTimeControlWindow|None = None
        # 파라미터 위젯 보관 {key: widget}
        self._param_widgets: dict[str, dict] = {}
        self._setup_styles(); self._build_ui()
        self._on_attack_change()
        self._set_ch("generated",False); self._set_ch("file",False)
        self._poll(); self.protocol("WM_DELETE_WINDOW",self._close)

    # ── 스타일 ─────────────────────────────────────
    def _setup_styles(self):
        s=ttk.Style(self); s.theme_use("clam")
        bg,ac,fg="#09111d","#35d0ff","#edf4ff"
        sb,eb,hi="#9db0c7","#172334","#24354d"
        s.configure(".",background=bg,foreground=fg,font=("Consolas",10))
        s.configure("TFrame",background=bg); s.configure("TLabel",background=bg,foreground=fg)
        s.configure("H.TLabel", background=bg,foreground=ac,  font=("Consolas",11,"bold"))
        s.configure("A.TLabel", background=bg,foreground="#ffcc44",font=("Consolas",10,"bold"))
        s.configure("ML.TLabel",background=bg,foreground="#ff9f44",font=("Consolas",10,"bold"))
        s.configure("ADV.TLabel",background=bg,foreground="#ff4488",font=("Consolas",10,"bold"))
        s.configure("Sub.TLabel",background=bg,foreground=sb,font=("Consolas",9))
        s.configure("TEntry",fieldbackground=eb,foreground="#ffffff",insertcolor=ac,borderwidth=0)
        s.configure("TSpinbox",fieldbackground=eb,foreground="#ffffff",
                    background=eb,arrowcolor=ac,borderwidth=0)
        s.configure("TCombobox",fieldbackground=eb,foreground="#ffffff",
                    selectbackground=ac,selectforeground=bg)
        s.map("TCombobox",fieldbackground=[("readonly",eb)],foreground=[("readonly","#ffffff")])
        s.configure("TCheckbutton",background=bg,foreground=fg)
        s.map("TCheckbutton",background=[("active",bg)])

    # ── 공통 빌더 ───────────────────────────────────
    def _section(self,p,t,style="H.TLabel"):
        f=ttk.Frame(p); f.pack(fill="x",padx=10,pady=(10,2))
        ttk.Label(f,text=t,style=style).pack(anchor="w")
        tk.Frame(p,height=1,bg="#24354d").pack(fill="x",padx=10,pady=(0,4))

    def _row(self,p,label,factory,**kw):
        row=ttk.Frame(p); row.pack(fill="x",padx=16,pady=2)
        ttk.Label(row,text=label,width=26,anchor="w",style="Sub.TLabel").pack(side="left")
        w=factory(row,**kw); w.pack(side="left",fill="x",expand=True); return w

    def _entry(self,p,default="",**kw):
        var=tk.StringVar(value=str(default)); e=ttk.Entry(p,textvariable=var,**kw)
        e._var=var; return e

    def _spin(self,p,lo,hi,default,step=1.0):
        var=tk.DoubleVar(value=default)
        s=ttk.Spinbox(p,from_=lo,to=hi,increment=step,textvariable=var,font=("Consolas",10))
        s._var=var; return s

    def _combo(self,p,values,default):
        var=tk.StringVar(value=default)
        c=ttk.Combobox(p,textvariable=var,values=values,state="readonly",font=("Consolas",10))
        c._var=var; return c

    # ── UI 빌드 ─────────────────────────────────────
    def _build_ui(self):
        tk.Label(self,text="  OPENCPN AIS IDS SIGNAL GENERATOR  v6",
                 bg="#35d0ff",fg="#08101a",
                 font=("Consolas",14,"bold"),padx=10,pady=8).pack(fill="x")
        tk.Label(self,
                 text="  ML-Aware AIS Attack Simulator  |  "
                      "Plugin Architecture  |  A~F Category (총 "+str(len(AttackRegistry.all()))+"개 패턴)",
                 bg="#112033",fg="#8aa1bb",
                 font=("Consolas",9),padx=10,pady=3).pack(fill="x")

        main=ttk.Frame(self); main.pack(fill="both",expand=True)
        left=ttk.Frame(main); left.pack(side="left",fill="both",expand=True)
        tk.Frame(main,width=1,bg="#24354d").pack(side="left",fill="y")
        right=ttk.Frame(main); right.pack(side="right",fill="both",expand=True)

        # 스크롤 캔버스
        canvas=tk.Canvas(left,bg="#09111d",highlightthickness=0)
        vbar=ttk.Scrollbar(left,orient="vertical",command=canvas.yview)
        self._sf=ttk.Frame(canvas)
        self._sfwin=canvas.create_window((0,0),window=self._sf,anchor="nw")
        self._sf.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfig(self._sfwin,width=e.width))
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right",fill="y"); canvas.pack(side="left",fill="both",expand=True)
        canvas.bind_all("<MouseWheel>",
                        lambda e:canvas.yview_scroll(-1*(e.delta//120),"units"))

        sf=self._sf
        self._build_network(sf)
        self._build_center(sf)
        self._build_movement(sf)
        self._build_attack_selector(sf)
        self._build_all_param_frames(sf)
        self._build_extra(sf)
        self._build_file(sf)
        self._build_controls(sf)

        # 로그
        tk.Label(right,text="  LIVE LOG",bg="#09111d",fg="#35d0ff",
                 font=("Consolas",11,"bold"),padx=12,pady=8).pack(fill="x")
        self._log=scrolledtext.ScrolledText(
            right,bg="#051019",fg="#dff7ff",font=("Consolas",9),
            insertbackground="#35d0ff",selectbackground="#1b3555",
            relief="flat",borderwidth=0,wrap="word")
        self._log.pack(fill="both",expand=True,padx=8,pady=(0,8))
        self._log.tag_config("info",foreground="#c6d6ea")
        self._log.tag_config("start",foreground="#35d0ff")
        self._log.tag_config("error",foreground="#ff6b6b")

    def _build_network(self,p):
        self._section(p,"네트워크")
        self._host  =self._row(p,"대상 IP",          self._entry,default="127.0.0.1")
        self._port  =self._row(p,"UDP 포트",          self._entry,default="1111")
        self._itv   =self._row(p,"송신 주기 (초)",   self._spin,lo=0.2,hi=120,default=2.0,step=0.1)

    def _build_center(self,p):
        self._section(p,"기준 좌표")
        self._lat=self._row(p,"중심 위도",self._entry,default="37.00")
        self._lon=self._row(p,"중심 경도",self._entry,default="21.00")

    def _build_movement(self,p):
        self._section(p,"★ 선단 이동 제어",style="A.TLabel")
        self._mv_spd=self._row(p,"이동 속도 (kn)",self._spin,lo=0,hi=30,default=0,step=0.5)
        self._mv_hdg=self._row(p,"이동 방향 (도)",self._spin,lo=0,hi=359,default=0,step=5)
        self._mv_acc=self._row(p,"가속도 (kn/분)", self._spin,lo=0,hi=5,default=0,step=0.1)

    def _build_attack_selector(self,p):
        self._section(p,"공격 패턴 선택")
        row=ttk.Frame(p); row.pack(fill="x",padx=16,pady=4)
        self._atk_var=tk.StringVar(value=AttackRegistry.labels()[0])
        cb=ttk.Combobox(row,textvariable=self._atk_var,
                        values=AttackRegistry.labels(),state="readonly",
                        font=("Consolas",11))
        cb.pack(fill="x"); cb.bind("<<ComboboxSelected>>",self._on_attack_change)
        # 패턴 설명 레이블
        self._meta_lbl=ttk.Label(p,text="",style="Sub.TLabel",wraplength=480)
        self._meta_lbl.pack(anchor="w",padx=16,pady=(0,4))

    def _build_all_param_frames(self,p):
        """모든 AttackPlugin의 param_defs로 GUI 패널을 동적 생성"""
        for plugin in AttackRegistry.all():
            key=plugin.meta.key
            frame=ttk.Frame(p); frame.pack(fill="x")
            self._param_widgets[key]={}
            cat_style={"A":"H.TLabel","B":"H.TLabel","C":"A.TLabel",
                       "D":"ML.TLabel","E":"ADV.TLabel","F":"ADV.TLabel"
                       }.get(plugin.meta.category,"H.TLabel")
            self._section(frame,f"{plugin.meta.label}  파라미터",style=cat_style)
            for pd in plugin.param_defs():
                if pd["type"]=="spin":
                    w=self._row(frame,pd["label"],self._spin,
                                lo=pd["min"],hi=pd["max"],
                                default=pd["default"],step=pd.get("step",1.0))
                elif pd["type"]=="combo":
                    w=self._row(frame,pd["label"],self._combo,
                                values=pd["values"],default=pd["default"])
                else:  # entry
                    w=self._row(frame,pd["label"],self._entry,default=str(pd["default"]))
                self._param_widgets[key][pd["key"]]=w
            # 패턴 숨김 기본
            frame.pack_forget()
            plugin._frame=frame  # type: ignore

    def _build_extra(self,p):
        self._section(p,"추가 옵션")
        row=ttk.Frame(p); row.pack(fill="x",padx=16,pady=4)
        self._anchor=tk.BooleanVar(value=False)
        ttk.Checkbutton(row,text="중앙 정박선 추가 (MMSI: 440123456)",
                        variable=self._anchor).pack(anchor="w")

    def _build_file(self,p):
        self._section(p,"정상 신호 파일")
        frow=ttk.Frame(p); frow.pack(fill="x",padx=16,pady=2)
        ttk.Label(frow,text="NMEA 파일",width=26,anchor="w",style="Sub.TLabel").pack(side="left")
        self._fpath=tk.StringVar(value=str(_DEFAULT_FILE) if _DEFAULT_FILE.exists() else "")
        ttk.Entry(frow,textvariable=self._fpath).pack(side="left",fill="x",expand=True)
        tk.Button(frow,text="찾기",bg="#172334",fg="#edf4ff",relief="flat",
                  padx=10,pady=3,command=self._browse).pack(side="left",padx=(6,0))
        self._fitv=self._row(p,"문장 간격 (초)",self._spin,lo=0.01,hi=5,default=0.1,step=0.01)
        rrow=ttk.Frame(p); rrow.pack(fill="x",padx=16,pady=4)
        self._frep=tk.BooleanVar(value=False)
        ttk.Checkbutton(rrow,text="파일 끝 후 반복",variable=self._frep).pack(anchor="w")

    def _build_controls(self,p):
        ttk.Separator(p,orient="horizontal").pack(fill="x",padx=10,pady=8)
        c=ttk.Frame(p); c.pack(fill="x",padx=10,pady=(0,4))

        def btn_row(parent,lbl,start_text,stop_text,start_cmd,stop_cmd,
                    start_bg,stop_fg):
            row=ttk.Frame(parent); row.pack(fill="x",pady=(0,5))
            ttk.Label(row,text=lbl,width=10,anchor="w",style="Sub.TLabel").pack(side="left")
            sb=tk.Button(row,text=start_text,bg=start_bg,fg="#08101a",
                         font=("Consolas",10,"bold"),relief="flat",cursor="hand2",
                         padx=12,pady=6,command=start_cmd)
            sb.pack(side="left",fill="x",expand=True,padx=(0,4))
            xb=tk.Button(row,text=stop_text,bg="#172334",fg=stop_fg,
                         font=("Consolas",10,"bold"),relief="flat",cursor="hand2",
                         padx=12,pady=6,command=stop_cmd)
            xb.pack(side="left",fill="x",expand=True,padx=(4,0))
            return sb,xb

        self._gstart,self._gstop=btn_row(c,"생성","생성 시작","생성 중단",
            self._start_gen,self._stop_gen,"#35d0ff","#ff7c7c")
        self._fstart,self._fstop=btn_row(c,"파일","파일 시작","파일 중단",
            self._start_file,self._stop_file,"#7ef0c9","#ffb36b")

        self._allstop=tk.Button(c,text="전체 중단",bg="#24354d",fg="#edf4ff",
                                font=("Consolas",10,"bold"),relief="flat",cursor="hand2",
                                padx=12,pady=6,command=self._stop_all)
        self._allstop.pack(fill="x",pady=(0,5))

        self._rtbtn=tk.Button(c,text="🎮  실시간 조작 창",bg="#1a2a1a",fg="#44ff88",
                              font=("Consolas",10,"bold"),relief="flat",cursor="hand2",
                              padx=12,pady=6,command=self._open_rt)
        self._rtbtn.pack(fill="x",pady=(0,10))

    # ── 이벤트 ─────────────────────────────────────
    def _on_attack_change(self,event=None):
        lbl=self._atk_var.get()
        key=AttackRegistry.key_by_label(lbl)
        plugin=AttackRegistry.get(key)
        for p in AttackRegistry.all():
            f=getattr(p,"_frame",None)
            if f:
                if p.meta.key==key:
                    if not f.winfo_manager(): f.pack(fill="x")
                else:
                    if f.winfo_manager(): f.pack_forget()
        # 메타 설명 업데이트
        m=plugin.meta
        self._meta_lbl.config(
            text=f"목적: {m.purpose}\n회피: {m.evasion}")

    def _browse(self):
        f=filedialog.askopenfilename(
            title="NMEA 파일 선택",
            filetypes=[("NMEA","*.txt *.nmea *.log"),("All","*.*")])
        if f: self._fpath.set(f)

    def _open_rt(self):
        if self._rt_win is None or not self._rt_win.winfo_exists():
            self._rt_win=RealTimeControlWindow(self)
        else:
            self._rt_win.lift(); self._rt_win.focus_force()

    # ── cfg 수집 ────────────────────────────────────
    def _common_cfg(self)->dict:
        h=self._host._var.get().strip()
        if not h: raise ValueError("IP를 입력하세요")
        return {"host":h,"port":int(self._port._var.get())}

    def _gen_cfg(self)->dict:
        cfg=self._common_cfg()
        itv=float(self._itv._var.get())
        if itv<=0: raise ValueError("주기 > 0")
        lbl=self._atk_var.get(); key=AttackRegistry.key_by_label(lbl)
        cfg.update({"interval":itv,"clat":float(self._lat._var.get()),
                    "clon":float(self._lon._var.get()),
                    "attack_key":key,"attack_label":lbl,
                    "add_anchor":self._anchor.get(),
                    "move_speed":float(self._mv_spd._var.get()),
                    "move_heading":float(self._mv_hdg._var.get()),
                    "move_accel":float(self._mv_acc._var.get())})
        # 현재 패턴 파라미터
        wmap=self._param_widgets.get(key,{})
        for pkey,w in wmap.items():
            try: cfg[pkey]=float(w._var.get())
            except: cfg[pkey]=w._var.get()
        return cfg

    def _file_cfg(self)->dict:
        cfg=self._common_cfg()
        fp=self._fpath.get().strip()
        if not Path(fp).exists(): raise ValueError("파일 없음")
        itv=float(self._fitv._var.get())
        if itv<=0: raise ValueError("파일 간격 > 0")
        cfg.update({"file_path":fp,"file_interval":itv,"file_repeat":self._frep.get()})
        return cfg

    # ── 채널 제어 ───────────────────────────────────
    def _any_running(self)->bool:
        return (self._gen_thread is not None  and self._gen_thread.is_alive() or
                self._file_thread is not None and self._file_thread.is_alive())

    def _set_ch(self,ch:str,running:bool):
        if ch=="generated":
            if running:
                self._gstart.config(state="disabled",bg="#172334",fg="#5f738c")
                self._gstop.config(state="normal")
            else:
                self._gstart.config(state="normal",bg="#35d0ff",fg="#08101a")
                self._gstop.config(state="disabled")
        else:
            if running:
                self._fstart.config(state="disabled",bg="#172334",fg="#5f738c")
                self._fstop.config(state="normal")
            else:
                self._fstart.config(state="normal",bg="#7ef0c9",fg="#08101a")
                self._fstop.config(state="disabled")
        self._allstop.config(state="normal" if self._any_running() else "disabled")

    def _start_gen(self):
        if self._gen_thread and self._gen_thread.is_alive():
            self._log_msg("이미 실행 중","error"); return
        try: cfg=self._gen_cfg()
        except ValueError as e: messagebox.showerror("입력 오류",str(e)); return
        self._gen_stop=threading.Event(); self._set_ch("generated",True)
        self._gen_thread=threading.Thread(
            target=sender_worker,args=("generated",cfg,self._gen_stop),daemon=True)
        self._gen_thread.start()

    def _stop_gen(self):
        if self._gen_thread and self._gen_thread.is_alive():
            self._gen_stop.set()

    def _start_file(self):
        if self._file_thread and self._file_thread.is_alive():
            self._log_msg("파일 이미 실행 중","error"); return
        try: cfg=self._file_cfg()
        except ValueError as e: messagebox.showerror("입력 오류",str(e)); return
        self._file_stop=threading.Event(); self._set_ch("file",True)
        self._file_thread=threading.Thread(
            target=sender_worker,args=("file",cfg,self._file_stop),daemon=True)
        self._file_thread.start()

    def _stop_file(self):
        if self._file_thread and self._file_thread.is_alive():
            self._file_stop.set()

    def _stop_all(self): self._stop_gen(); self._stop_file()

    def _log_msg(self,msg:str,level:str="info"):
        ts=time.strftime("%H:%M:%S")
        self._log.insert("end",f"[{ts}] {msg}\n",level); self._log.see("end")

    def _poll(self):
        while not _LOG_Q.empty():
            item=_LOG_Q.get_nowait()
            if item.get("kind")=="chan" and item.get("state")=="finished":
                self._set_ch(item["channel"],False); continue
            self._log_msg(item.get("message",""),item.get("level","info"))
        self.after(200,self._poll)

    def _close(self): self._gen_stop.set(); self._file_stop.set(); self.destroy()


# ═══════════════════════════════════════════════════
#  §9  진입점
# ═══════════════════════════════════════════════════
if __name__=="__main__":
    App().mainloop()