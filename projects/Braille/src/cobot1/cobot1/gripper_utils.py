import time

DO_BLUE = 1
DO_PURPLE = 2
DI_GRIP_CHECK = 2

def _get_dsr():
    import DSR_ROBOT2
    return DSR_ROBOT2

def open_gripper(wait_time=0.5):
    dsr = _get_dsr()
    dsr.set_digital_output(DO_BLUE, 0)
    dsr.set_digital_output(DO_PURPLE, 1)
    time.sleep(wait_time)

def close_gripper(wait_time=1.0):
    dsr = _get_dsr()
    dsr.set_digital_output(DO_BLUE, 1)
    dsr.set_digital_output(DO_PURPLE, 0)
    time.sleep(wait_time)

def is_grip_success():
    dsr = _get_dsr()
    grip_state = dsr.get_digital_input(DI_GRIP_CHECK)
    print(f"[GRIP CHECK] input={grip_state}")
    return grip_state == 1

def close_and_check(wait_time=1.0):
    close_gripper(wait_time)
    return is_grip_success()

def require_grip_success():
    ok = is_grip_success()

    if not ok:
        raise RuntimeError("그리퍼 파지 실패 (DI 신호 없음)")

    return True