import threading


config_sync_requested = threading.Event()


def request_config_sync() -> None:
    config_sync_requested.set()


def wait_for_config_sync(timeout: int) -> bool:
    requested = config_sync_requested.wait(timeout=timeout)

    if requested:
        config_sync_requested.clear()

    return requested
