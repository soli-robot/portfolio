from __future__ import annotations

import os
import math
import queue
import socketserver
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Optional

import omni.ext
import omni.kit.app
import omni.usd
import omni.timeline  
from pxr import Usd, UsdGeom, Gf, UsdPhysics

# =============================================================================
# TCP command server settings
# =============================================================================
HOST = "127.0.0.1"
PORT = 8765

# =============================================================================
# Stage paths
# =============================================================================
OBJECT_ROOT_PRIM_PATH = "/Root/box_aruco2"
ARUCO_PRIM_PATH = "/Root/box_aruco2/box_aruco2/aruco"
SUCTION_TCP_PRIM_PATH = ""

ROBOT_ARTICULATION_ROOT_PATH_CANDIDATES = [
    "/exex/Nova_Carter_ROS/chassis_link",
]
TARGET_JOINT_NAMES = [
    "joint_1", "joint_2", "joint_3",
    "joint_4", "joint_5", "joint_6",
]

ZERO_JOINT_POS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

# 💡 이동을 위한 팔 접기 포즈
FOLDED_POSE = [3.14, -1.57, 1.57, 0.0, 1.57, 0.0]

ACTION_LIMIT = 3.14
OBS_CLAMP = 10.0
OBJECT_POS_CLAMP = 2.0
CONTROL_DT = 0.03
LOG_INTERVAL_FRAMES = 60
CLEAR_OBJECT_XFORM_ON_FIRST_SET = False

# =============================================================================
# Task profile settings
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PICK_POLICY_TORCHSCRIPT_PATH = "/home/rokey/Downloads/final_code_2/pick.pt"

PLACE_POLICY_TORCHSCRIPT_PATH = "/home/rokey/Downloads/final_code_2/place.pt"

PICK_DEFAULT_JOINT_POS = [3.14159, 0.0, 1.5708, 0.0, 0.0, -1.5708]
PICK_TARGET_JOINT_POS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
PICK_READY_JOINT_POS = [3.14159, 0.0, 1.5708, 0.0, 0.0, -1.5708]

PICK_READY_HOLD_SEC = 2.0
PICK_READY_CONTROL_DT = 0.05
PICK_ACTION_SCALE = 0.5
PICK_SUCTION_THRESHOLD = 0.25  
EMA_SMOOTH_FACTOR = 0.15 

PLACE_DEFAULT_JOINT_POS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
PLACE_ACTION_SCALE = 0.5
PLACE_TARGET_COMMAND_ROBOT_FRAME = [0.0, -0.95, 0.05, 1.0, 0.0, 0.0, 0.0]
PLACE_ATTACH_OFFSET_POS_EE = [0.0, 0.0, -0.075]
PLACE_ATTACH_OFFSET_QUAT_EE = [0.0, 0.0, 0.0, 1.0]

PLACE_RELEASE_XY_THRESHOLD = 0.04
PLACE_RELEASE_Z_THRESHOLD = 0.05

# =============================================================================
# Math utilities
# =============================================================================
def quat_conjugate_xyzw(q):
    return [-q[0], -q[1], -q[2], q[3]]

def quat_normalize_xyzw(q):
    norm = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if norm == 0.0:
        return [0.0, 0.0, 0.0, 1.0]
    return [q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm]

def quat_apply_xyzw(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    rx = vx + w * tx + (y * tz - z * ty)
    ry = vy + w * ty + (z * tx - x * tz)
    rz = vz + w * tz + (x * ty - y * tx)
    return [rx, ry, rz]

def quat_multiply_xyzw(q1, q2):
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return quat_normalize_xyzw([
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ])

def snap_quat_to_4way(quat_xyzw):
    x, y, z, w = quat_xyzw
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y**2 + z**2))
    snapped_yaw = round(yaw / (math.pi / 2.0)) * (math.pi / 2.0)
    half_yaw = snapped_yaw / 2.0
    return [0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)]

def vec_add(a, b): return [float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2])]
def vec_sub(a, b): return [float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2])]
def vec_norm(v): return math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2)
def clamp_list(values, low, high): return [max(min(float(v), high), low) for v in values]
def fmt_vec(v): return "[" + ", ".join(f"{float(x):.4f}" for x in v) + "]"

# =============================================================================
# Policy wrappers & Handlers
# =============================================================================
@dataclass
class TaskProfile:
    name: str
    model_type: str
    default_joint_pos: list
    action_scale: float
    policy_path: str = ""

class CommandHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(4096).decode("utf-8").strip()
        ext = self.server.extension_ref
        if not data:
            self.request.sendall(b"empty command\n")
            return
        print(f"[cobot3_policy_suction] TCP received: {data}")
        ext.command_queue.put(data)
        self.request.sendall(f"queued: {data}\n".encode("utf-8"))

# =============================================================================
# Extension
# =============================================================================
class Cobot3PolicySuctionExtension(omni.ext.IExt):
    def on_startup(self, ext_id):
        print("[cobot3_policy_suction] Extension startup")
        self.ext_id = ext_id
        self.command_queue = queue.Queue()

        self._update_sub = None
        self._timeline_sub = None
        self._server = None
        self._server_thread = None

        self.active_task: Optional[str] = None
        self.policy_running = False
        self.suction_running = False
        self.running = False
        self.pick_ready_running = False

        self.task_profiles = {
            "pick": TaskProfile("pick", "torchscript", PICK_DEFAULT_JOINT_POS, PICK_ACTION_SCALE, PICK_POLICY_TORCHSCRIPT_PATH),
            "place": TaskProfile("place", "torchscript", PLACE_DEFAULT_JOINT_POS, PLACE_ACTION_SCALE, PLACE_POLICY_TORCHSCRIPT_PATH),
        }

        self._loaded_policies = {}
        self._attached = False
        self._released_once = False
        self._root_from_marker_ee = None
        self._object_rel_quat_ee = None
        self._place_attach_offset_pos_ee = list(PLACE_ATTACH_OFFSET_POS_EE)
        self._place_attach_offset_quat_ee = list(PLACE_ATTACH_OFFSET_QUAT_EE)

        self._smoothed_cmd = None
        self._init_obj_pos = None
        self._init_obj_quat = None

        self._object_xform_initialized = False
        self._object_translate_op = None
        self._object_orient_op = None

        self._robot_root_path = None
        self._suction_tcp_path = None
        self._object_root_path = None
        self._aruco_path = None

        self._dc = None
        self._art = None

        self._torch = None
        self._last_action = [0.0] * 6
        self._last_policy_time = 0.0
        self._last_obs, self._last_obs_raw = None, None
        self._last_obj_ee, self._last_obj_robot = [0.0]*3, [0.0]*3
        self._object_visible = 0.0
        self._last_dist = None
        self._frame_count = 0

        self._pick_ready_start_time = 0.0
        self._pick_ready_last_update_time = 0.0

        self._start_command_server()
        self._start_update_loop()

        print(f"[cobot3_policy_suction] TCP command server: {HOST}:{PORT}")

    def on_shutdown(self):
        print("[cobot3_policy_suction] Extension shutdown")
        self.policy_running = False
        self.suction_running = False
        self.running = False
        self.pick_ready_running = False
        self._update_sub = None
        self._timeline_sub = None

        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception: pass
        self._server = None

    def _start_command_server(self):
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer((HOST, PORT), CommandHandler)
        self._server.extension_ref = self
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

    def _start_update_loop(self):
        app = omni.kit.app.get_app()
        self._update_sub = app.get_update_event_stream().create_subscription_to_pop(self._on_update, name="cobot3_policy_suction_task_manager_update")
        self._timeline_sub = omni.timeline.get_timeline_interface().get_timeline_event_stream().create_subscription_to_pop(self._on_timeline_event)
        print("[cobot3_policy_suction] update & timeline subscriptions registered")

    def _on_timeline_event(self, e):
        if e.type == int(omni.timeline.TimelineEventType.STOP):
            self.policy_running = False
            self.suction_running = False
            self.running = False
            self._attached = False
            self._force_detach() 
            self._restore_object_initial_pose()

    def _on_update(self, event):
        self._frame_count += 1
        try:
            self._process_commands()
        except Exception:
            print(traceback.format_exc())

        if self.pick_ready_running:
            now = time.monotonic()
            if now - self._pick_ready_last_update_time >= PICK_READY_CONTROL_DT:
                self._pick_ready_last_update_time = now
                try: self._pick_ready_update()
                except: self.pick_ready_running = False

        if self.suction_running:
            try:
                self._save_initial_object_pose()
                self._update_active_suction()
            except Exception:
                pass

        if self.policy_running:
            now = time.monotonic()
            if now - self._last_policy_time >= CONTROL_DT:
                self._last_policy_time = now
                try: self._policy_step_once(apply_action=True, verbose=False)
                except Exception:
                    print(traceback.format_exc())
                    self.policy_running = False

    def _process_commands(self):
        processed = 0
        while not self.command_queue.empty():
            processed += 1
            cmd = self.command_queue.get_nowait().strip().lower()
            print(f"[cobot3_policy_suction] processing command: {cmd}")

            try:
                if cmd == "ping": print("[cobot3_policy_suction] pong")
                elif cmd == "task_list": self._print_task_list()
                elif cmd == "task_status": self._print_task_status()
                elif cmd == "run_pick": self._run_task("pick")
                elif cmd == "pick_ready": self._start_pick_ready_only()
                elif cmd == "pick_start_policy": self._start_pick_policy_after_ready()
                elif cmd == "run_place": self._run_task("place")
                elif cmd == "stop":
                    self.policy_running = False
                    self.suction_running = False
                    self.running = False
                    self.pick_ready_running = False
                    self._force_detach() 

                elif cmd == "reset":
                    self._reset_active_task()
                    self._restore_object_initial_pose()
                elif cmd == "hold_home":
                    profile = self._get_active_profile_or_default()
                    self._set_joint_targets(profile.default_joint_pos)
                elif cmd == "hold_zero": self._set_joint_targets(ZERO_JOINT_POS)
                
                elif cmd == "policy_stop":
                    self.policy_running = False
                    self.pick_ready_running = False
                    
                    if self.active_task == "pick" and not self._attached:
                        print("[cobot3_policy_suction] policy_stop received but not attached -> Forcing attach!")
                        self._force_pick_attach()
                        
                    if self._attached:
                        self.suction_running = True
                        self.running = True
                        print("[cobot3_policy_suction] Moving to FOLDED_POSE for safe navigation.")
                        self._set_joint_targets(FOLDED_POSE)
                        self._set_object_collision(False)
                        
                elif cmd == "force_attach":
                    if self.active_task == "place": self._reset_place_task()
                    else:
                        self.active_task = self.active_task or "pick"
                        self._force_pick_attach()
                    self.suction_running = True
                    self.running = True
                elif cmd == "force_detach": self._force_detach()
                elif cmd == "start":
                    if self.active_task is None: self.active_task = "pick"
                    self.suction_running = True
                    self.running = True
            except Exception:
                print(f"[cobot3_policy_suction] command '{cmd}' failed\n{traceback.format_exc()}")

    # --- Print & Log functions ---
    def _print_task_list(self): pass
    def _print_task_status(self): pass

    def _run_task(self, task_name: str):
        if task_name not in self.task_profiles: raise RuntimeError(f"Unknown task: {task_name}")
        previous_task, was_attached = self.active_task, bool(self._attached)
        self.policy_running = self.suction_running = self.running = self.pick_ready_running = False
        self.active_task = task_name
        self._last_action = [0.0] * 6
        self._last_policy_time = 0.0

        self._load_active_policy()

        if task_name == "pick":
            self._reset_active_task(start_after_reset=False)
            self.suction_running, self.running, self.pick_ready_running = True, True, True
            self._pick_ready_start_time = time.monotonic()
            self._pick_ready_last_update_time = 0.0
            self._set_joint_targets(PICK_READY_JOINT_POS)
            return

        if task_name == "place":
            if was_attached and previous_task == "pick":
                self._capture_current_object_offset_for_place()
                self._released_once = False
            else:
                self._reset_active_task(start_after_reset=False)
            self.suction_running, self.running, self.policy_running = True, True, True

    def _reset_active_task(self, start_after_reset: bool = False):
        if self.active_task is None: return
        self.policy_running = self.suction_running = self.running = self.pick_ready_running = False
        self._last_action = [0.0] * 6
        self._smoothed_cmd = None 
        self._reset_suction_state()
        if self.active_task == "pick": self._reset_pick_task()
        elif self.active_task == "place": self._reset_place_task()
        if start_after_reset:
            self.suction_running = self.running = True

    def _get_active_profile_or_default(self) -> TaskProfile:
        return self.task_profiles[self.active_task] if self.active_task else self.task_profiles["pick"]

    # -------------------------------------------------------------------------
    # Stage & Path Resolvers
    # -------------------------------------------------------------------------
    def _get_stage(self):
        stage = omni.usd.get_context().get_stage()
        if stage is None: raise RuntimeError("현재 열린 USD stage가 없습니다.")
        return stage

    def _ensure_prim_valid(self, path: str):
        prim = self._get_stage().GetPrimAtPath(path)
        if not prim.IsValid(): raise RuntimeError(f"Invalid prim path: {path}")
        return prim

    def _find_prim_by_name(self, stage, name: str) -> Optional[str]:
        for prim in stage.Traverse():
            if prim.GetName() == name: return str(prim.GetPath())
        return None

    def _has_all_target_dofs(self, art) -> bool:
        dc = self._get_dynamic_control()
        for name in TARGET_JOINT_NAMES:
            if dc.find_articulation_dof(art, name) is None: return False
        return True

    def _resolve_robot_root_path(self) -> str:
        dc = self._get_dynamic_control()
        if self._robot_root_path is not None:
            try:
                art = dc.get_articulation(self._robot_root_path)
                if art and self._has_all_target_dofs(art):
                    self._art = art
                    return self._robot_root_path
            except: pass
            self._robot_root_path = self._art = None

        stage = self._get_stage()
        for path in ROBOT_ARTICULATION_ROOT_PATH_CANDIDATES:
            if stage.GetPrimAtPath(path).IsValid():
                art = dc.get_articulation(path)
                if art and self._has_all_target_dofs(art):
                    self._robot_root_path = path
                    self._art = art
                    return path

        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                path = str(prim.GetPath())
                art = dc.get_articulation(path)
                if art and self._has_all_target_dofs(art):
                    self._robot_root_path = path
                    self._art = art
                    return path
        raise RuntimeError("joint_1~joint_6 root를 찾지 못했습니다.")

    def _resolve_suction_tcp_path(self) -> str:
        if self._suction_tcp_path: return self._suction_tcp_path
        stage = self._get_stage()
        if SUCTION_TCP_PRIM_PATH and stage.GetPrimAtPath(SUCTION_TCP_PRIM_PATH).IsValid():
            self._suction_tcp_path = SUCTION_TCP_PRIM_PATH
            return SUCTION_TCP_PRIM_PATH
        found = self._find_prim_by_name(stage, "suction_tcp")
        if found is None: raise RuntimeError("suction_tcp prim을 찾지 못했습니다.")
        self._suction_tcp_path = found
        return found

    def _resolve_object_root_path(self) -> str:
        if self._object_root_path: return self._object_root_path
        stage = self._get_stage()
        if stage.GetPrimAtPath(OBJECT_ROOT_PRIM_PATH).IsValid():
            self._object_root_path = OBJECT_ROOT_PRIM_PATH
            return self._object_root_path
        found = self._find_prim_by_name(stage, "SM_CardBoxC_01")
        if found is None: raise RuntimeError("object root를 찾지 못했습니다.")
        self._object_root_path = found
        return found

    def _resolve_aruco_path(self) -> str:
        if self._aruco_path: return self._aruco_path
        stage = self._get_stage()
        if stage.GetPrimAtPath(ARUCO_PRIM_PATH).IsValid():
            self._aruco_path = ARUCO_PRIM_PATH
            return self._aruco_path
        found = self._find_prim_by_name(stage, "aruco")
        if found is None: raise RuntimeError("aruco prim을 찾지 못했습니다.")
        self._aruco_path = found
        return found

    def _get_prim_world_pose_xyzw(self, prim_path: str):
        prim = self._ensure_prim_valid(prim_path)
        mat = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        pos = mat.ExtractTranslation()
        quat = mat.ExtractRotationQuat()
        return [float(pos[0]), float(pos[1]), float(pos[2])], quat_normalize_xyzw([float(quat.GetImaginary()[0]), float(quat.GetImaginary()[1]), float(quat.GetImaginary()[2]), float(quat.GetReal())])

    # -------------------------------------------------------------------------
    # Physics & Robot Control
    # -------------------------------------------------------------------------
    def _get_dynamic_control(self):
        if self._dc is not None: return self._dc
        from omni.isaac.dynamic_control import _dynamic_control
        self._dc = _dynamic_control.acquire_dynamic_control_interface()
        return self._dc

    def _get_articulation(self):
        dc = self._get_dynamic_control()
        if self._robot_root_path is not None:
            try:
                art = dc.get_articulation(self._robot_root_path)
                if art and self._has_all_target_dofs(art):
                    self._art = art
                    return art
            except: pass
            self._art = self._robot_root_path = None
        self._resolve_robot_root_path()
        return self._art

    def _get_joint_positions_velocities(self):
        dc = self._get_dynamic_control()
        art = self._get_articulation()
        from omni.isaac.dynamic_control import _dynamic_control
        pos, vel = [], []
        for name in TARGET_JOINT_NAMES:
            dof = dc.find_articulation_dof(art, name)
            state = dc.get_dof_state(dof, _dynamic_control.STATE_ALL)
            pos.append(float(state.pos)); vel.append(float(state.vel))
        return pos, vel

    def _set_joint_targets(self, targets):
        dc = self._get_dynamic_control()
        art = self._get_articulation()
        try: dc.wake_up_articulation(art)
        except: pass
        for name, target in zip(TARGET_JOINT_NAMES, targets):
            dof = dc.find_articulation_dof(art, name)
            dc.set_dof_position_target(dof, float(target))

    def _zero_out_object_velocity(self):
        try:
            dc = self._get_dynamic_control()
            rb = dc.get_rigid_body(self._resolve_object_root_path())
            if rb:
                dc.set_rigid_body_linear_velocity(rb, [0.0, 0.0, 0.0])
                dc.set_rigid_body_angular_velocity(rb, [0.0, 0.0, 0.0])
        except: pass

    def _set_object_collision(self, enabled: bool):
        try:
            stage = self._get_stage()
            prim = stage.GetPrimAtPath(self._resolve_object_root_path())
            for p in Usd.PrimRange(prim):
                if p.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Set(enabled)
            print(f"[cobot3_policy_suction] Object collision forced {'ON' if enabled else 'OFF'}.")
        except Exception as e:
            print(f"[cobot3_policy_suction] Failed to set object collision: {e}")

    # 💡 [수정 3] 놓아줄 때 중력 영향을 받게 하기 위한 Kinematic 상태 제어 헬퍼
    def _set_object_kinematic(self, enabled: bool):
        try:
            prim = self._get_stage().GetPrimAtPath(self._resolve_object_root_path())
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Set(enabled)
        except: pass

    def _wake_up_object(self):
        try:
            dc = self._get_dynamic_control()
            rb = dc.get_rigid_body(self._resolve_object_root_path())
            if rb: dc.wake_up_rigid_body(rb)
        except: pass

    def _save_initial_object_pose(self):
        if self._init_obj_pos is None:
            try: self._init_obj_pos, self._init_obj_quat = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
            except: pass

    def _restore_object_initial_pose(self):
        if self._init_obj_pos is not None:
            try:
                self._set_object_root_pose_xyzw(self._init_obj_pos, self._init_obj_quat)
                self._zero_out_object_velocity()
                self._set_object_collision(True)
                # 💡 리셋 시 Kinematic 모드 해제
                self._set_object_kinematic(False)
                # 💡 리셋 시 물리 엔진을 깨워 상자가 바닥으로 안착하도록 만듦
                self._wake_up_object()
            except: pass

    # -------------------------------------------------------------------------
    # Policy loading / inference
    # -------------------------------------------------------------------------
    def _load_active_policy(self):
        if self.active_task in self._loaded_policies: return
        profile = self.task_profiles[self.active_task]
        import torch
        self._torch = torch
        policy = torch.jit.load(profile.policy_path, map_location="cpu")
        policy.eval()
        self._loaded_policies[profile.name] = policy

    def _active_policy(self):
        self._load_active_policy()
        return self._loaded_policies[self.active_task]

    def _build_observation(self):
        if self.active_task == "pick": return self._build_pick_observation()
        if self.active_task == "place": return self._build_place_observation()

    def _build_pick_observation(self):
        profile = self.task_profiles["pick"]
        pos, vel = self._get_joint_positions_velocities()
        object_position_ee, object_visible = self._compute_object_position_ee()
        obs_list = [pos[i] - profile.default_joint_pos[i] for i in range(6)] + list(vel) + object_position_ee + [object_visible] + [PICK_TARGET_JOINT_POS[i] - pos[i] for i in range(6)] + self._last_action
        
        obs_tensor = self._torch.nan_to_num(self._torch.tensor([obs_list], dtype=self._torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
        self._last_obs = obs_tensor
        return obs_tensor

    def _build_place_observation(self):
        profile = self.task_profiles["place"]
        pos, vel = self._get_joint_positions_velocities()
        object_position_robot = self._compute_object_position_robot_root()
        obs_list = [pos[i] - profile.default_joint_pos[i] for i in range(6)] + list(vel) + object_position_robot + PLACE_TARGET_COMMAND_ROBOT_FRAME + self._last_action
        
        obs_tensor = self._torch.nan_to_num(self._torch.tensor([obs_list], dtype=self._torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
        self._last_obs = obs_tensor
        return obs_tensor

    def _policy_step_once(self, apply_action: bool, verbose: bool):
        profile = self.task_profiles[self.active_task]
        with self._torch.no_grad():
            action_tensor = self._active_policy()(self._build_observation())

        raw_action = clamp_list(action_tensor[0].detach().cpu().tolist()[:6], -ACTION_LIMIT, ACTION_LIMIT)
        self._last_action = raw_action

        cmd_pos = [profile.default_joint_pos[i] + raw_action[i] * profile.action_scale for i in range(6)]
        cmd_pos = clamp_list(cmd_pos, -ACTION_LIMIT, ACTION_LIMIT)

        # 💡 [수정 1] Place 시 AI 목표 각도의 1번 Joint에서 -90도(-1.5708 rad)를 빼서 직접 퍼블리시
        if self.active_task == "place":
            cmd_pos[0] -= 1.708

        # 💡 [수정 2] Policy 첫 시작 시 현재 관절 위치에서 시작하여 튀어오름(Jerk) 방지
        if self._smoothed_cmd is None:
            current_pos, _ = self._get_joint_positions_velocities()
            self._smoothed_cmd = current_pos
        else:
            self._smoothed_cmd = [
                EMA_SMOOTH_FACTOR * cmd_pos[i] + (1.0 - EMA_SMOOTH_FACTOR) * self._smoothed_cmd[i]
                for i in range(6)
            ]

        if apply_action:
            self._set_joint_targets(self._smoothed_cmd)

    def _compute_object_position_ee(self):
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        marker_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_aruco_path())
        return clamp_list(quat_apply_xyzw(quat_conjugate_xyzw(ee_quat_w), vec_sub(marker_pos_w, ee_pos_w)), -OBJECT_POS_CLAMP, OBJECT_POS_CLAMP), 1.0

    def _compute_object_position_robot_root(self):
        robot_root_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_robot_root_path())
        object_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
        return vec_sub(object_pos_w, robot_root_pos_w)

    def _place_target_pos_world(self):
        root_pos_w, root_quat_w = self._get_prim_world_pose_xyzw(self._resolve_robot_root_path())
        return vec_add(root_pos_w, quat_apply_xyzw(root_quat_w, PLACE_TARGET_COMMAND_ROBOT_FRAME[:3]))

    # -------------------------------------------------------------------------
    # Pick ready & Suction State
    # -------------------------------------------------------------------------
    def _start_pick_ready_only(self):
        self.active_task = "pick"
        self._load_active_policy()
        self.policy_running = self.suction_running = self.running = False
        self.pick_ready_running = True
        self._pick_ready_start_time = time.monotonic()
        self._pick_ready_last_update_time = 0.0
        self._set_joint_targets(PICK_READY_JOINT_POS)

    def _start_pick_policy_after_ready(self):
        self.active_task = "pick"
        self._load_active_policy()
        self.pick_ready_running = False
        self.suction_running = self.running = self.policy_running = True

    def _pick_ready_update(self):
        self._set_joint_targets(PICK_READY_JOINT_POS)
        if time.monotonic() - self._pick_ready_start_time >= PICK_READY_HOLD_SEC:
            self._start_pick_policy_after_ready()

    def _reset_suction_state(self):
        self._attached = self._released_once = False
        self._root_from_marker_ee = self._object_rel_quat_ee = None
        self._place_attach_offset_pos_ee = list(PLACE_ATTACH_OFFSET_POS_EE)
        self._place_attach_offset_quat_ee = list(PLACE_ATTACH_OFFSET_QUAT_EE)
        self._last_dist = None

    def _reset_pick_task(self):
        self._set_joint_targets(PICK_READY_JOINT_POS)
        self._reset_suction_state()
        self._set_object_collision(True)

    def _reset_place_task(self):
        self._set_joint_targets(self.task_profiles["place"].default_joint_pos)
        self._reset_suction_state()
        self._attach_object_to_tcp_place_reset()

    def _update_active_suction(self):
        if self.active_task == "pick": return self._update_pick_suction()
        if self.active_task == "place": return self._update_place_suction()

    def _force_detach(self):
        self._attached = False
        self._released_once = True
        self._root_from_marker_ee = self._object_rel_quat_ee = None
        self._set_object_collision(True)
        # 💡 [수정 3] 놓아줄 때 Rigid Body Kinematic 모드를 꺼서 중력에 의해 떨어지도록 함
        self._set_object_kinematic(False)
        self._wake_up_object()

    def _force_pick_attach(self):
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        marker_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_aruco_path())
        obj_pos_w, obj_quat_w = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
        self._record_pick_attachment(ee_pos_w, ee_quat_w, marker_pos_w, obj_pos_w, obj_quat_w, vec_norm(vec_sub(ee_pos_w, marker_pos_w)))
        self._update_pick_suction()

    def _record_pick_attachment(self, ee_pos_w, ee_quat_w, marker_pos_w, obj_pos_w, obj_quat_w, dist):
        self._attached = True
        self._released_once = False
        ee_quat_inv = quat_conjugate_xyzw(ee_quat_w)
        self._root_from_marker_ee = quat_apply_xyzw(ee_quat_inv, vec_sub(obj_pos_w, marker_pos_w))
        self._object_rel_quat_ee = quat_multiply_xyzw(ee_quat_inv, obj_quat_w)

    def _update_pick_suction(self):
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        marker_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_aruco_path())
        dist = vec_norm(vec_sub(ee_pos_w, marker_pos_w))
        self._last_dist = dist

        if not self._attached and dist < PICK_SUCTION_THRESHOLD:
            obj_pos_w, obj_quat_w = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
            self._record_pick_attachment(ee_pos_w, ee_quat_w, marker_pos_w, obj_pos_w, obj_quat_w, dist)

            print("[cobot3_policy_suction] Auto-attached! Stopping policy and folding arm.")
            self.policy_running = False
            self.pick_ready_running = False
            self._set_joint_targets(FOLDED_POSE)
            self._set_object_collision(False)
            # 💡 [수정 3] 잡을 때 Kinematic 모드를 켜서 로봇 팔을 강제로 따라오게 함
            self._set_object_kinematic(True)

        if self._attached and self._root_from_marker_ee and self._object_rel_quat_ee:
            new_obj_pos_w = vec_add(ee_pos_w, quat_apply_xyzw(ee_quat_w, self._root_from_marker_ee))
            new_obj_quat_w = quat_multiply_xyzw(ee_quat_w, self._object_rel_quat_ee)
            self._set_object_root_pose_xyzw(new_obj_pos_w, new_obj_quat_w)
            self._zero_out_object_velocity()

    def _capture_current_object_offset_for_place(self):
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        obj_pos_w, obj_quat_w = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
        ee_quat_inv = quat_conjugate_xyzw(ee_quat_w)
        self._place_attach_offset_pos_ee = quat_apply_xyzw(ee_quat_inv, vec_sub(obj_pos_w, ee_pos_w))
        self._place_attach_offset_quat_ee = quat_multiply_xyzw(ee_quat_inv, obj_quat_w)
        self._attached, self._released_once = True, False

    def _attach_object_to_tcp_place_reset(self):
        self._place_attach_offset_pos_ee = list(PLACE_ATTACH_OFFSET_POS_EE)
        self._place_attach_offset_quat_ee = list(PLACE_ATTACH_OFFSET_QUAT_EE)
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        self._set_object_root_pose_xyzw(vec_add(ee_pos_w, quat_apply_xyzw(ee_quat_w, self._place_attach_offset_pos_ee)), quat_multiply_xyzw(ee_quat_w, self._place_attach_offset_quat_ee))
        self._attached, self._released_once = True, False

    def _release_place_if_near_target(self):
        if not self._attached: return False
        obj_pos_w, _ = self._get_prim_world_pose_xyzw(self._resolve_object_root_path())
        target_pos_w = self._place_target_pos_world()
        diff = vec_sub(obj_pos_w, target_pos_w)
        self._last_dist = vec_norm(diff)
        if math.sqrt(diff[0] ** 2 + diff[1] ** 2) < PLACE_RELEASE_XY_THRESHOLD and abs(diff[2]) < PLACE_RELEASE_Z_THRESHOLD:
            self._force_detach()
            return True
        return False

    def _update_place_suction(self):
        if self._release_place_if_near_target() or not self._attached: return
        ee_pos_w, ee_quat_w = self._get_prim_world_pose_xyzw(self._resolve_suction_tcp_path())
        new_obj_pos_w = vec_add(ee_pos_w, quat_apply_xyzw(ee_quat_w, self._place_attach_offset_pos_ee))
        new_obj_quat_w = quat_multiply_xyzw(ee_quat_w, self._place_attach_offset_quat_ee)
        
        self._set_object_root_pose_xyzw(new_obj_pos_w, new_obj_quat_w)
        self._zero_out_object_velocity()
        self._last_dist = vec_norm(vec_sub(new_obj_pos_w, self._place_target_pos_world()))

    # -------------------------------------------------------------------------
    # Object xform helpers
    # -------------------------------------------------------------------------
    def _find_existing_xform_op(self, xform: UsdGeom.Xformable, op_name_suffix: str):
        for op in xform.GetOrderedXformOps():
            if op.GetOpName().endswith(op_name_suffix): return op
        return None

    def _is_float_precision_op(self, op: UsdGeom.XformOp) -> bool:
        try:
            if op.GetPrecision() == UsdGeom.XformOp.PrecisionFloat: return True
            if op.GetPrecision() == UsdGeom.XformOp.PrecisionDouble: return False
        except Exception: pass
        try:
            type_name = str(op.GetAttr().GetTypeName()).lower()
            if "quatf" in type_name or "float" in type_name: return True
            if "quatd" in type_name or "double" in type_name: return False
        except Exception: pass
        return True

    def _init_object_xform_ops_if_needed(self):
        if self._object_xform_initialized: return
        xform = UsdGeom.Xformable(self._ensure_prim_valid(self._resolve_object_root_path()))
        if CLEAR_OBJECT_XFORM_ON_FIRST_SET: xform.ClearXformOpOrder()
        translate_op = self._find_existing_xform_op(xform, "xformOp:translate")
        orient_op = self._find_existing_xform_op(xform, "xformOp:orient")

        if translate_op is None: translate_op = xform.AddTranslateOp(precision=UsdGeom.XformOp.PrecisionFloat)
        if orient_op is None: orient_op = xform.AddOrientOp(precision=UsdGeom.XformOp.PrecisionFloat)
        
        try:
            ordered_ops = xform.GetOrderedXformOps()
            if translate_op not in ordered_ops or orient_op not in ordered_ops:
                xform.SetXformOpOrder([translate_op, orient_op])
        except Exception: pass

        self._object_translate_op = translate_op
        self._object_orient_op = orient_op
        self._object_xform_initialized = True

    def _set_object_root_pose_xyzw(self, pos_w, quat_w_xyzw):
        self._init_object_xform_ops_if_needed()
        x, y, z, w = quat_normalize_xyzw(quat_w_xyzw)
        if self._is_float_precision_op(self._object_translate_op):
            self._object_translate_op.Set(Gf.Vec3f(float(pos_w[0]), float(pos_w[1]), float(pos_w[2])))
        else:
            self._object_translate_op.Set(Gf.Vec3d(float(pos_w[0]), float(pos_w[1]), float(pos_w[2])))

        if self._is_float_precision_op(self._object_orient_op):
            self._object_orient_op.Set(Gf.Quatf(float(w), Gf.Vec3f(float(x), float(y), float(z))))
        else:
            self._object_orient_op.Set(Gf.Quatd(float(w), Gf.Vec3d(float(x), float(y), float(z))))

    # -------------------------------------------------------------------------
    # Status Loggers
    # -------------------------------------------------------------------------
    def _print_status(self): pass
    def _print_robot_status(self): pass
    def _print_joint_status(self): pass