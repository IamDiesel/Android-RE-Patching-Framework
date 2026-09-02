import os
from core.infrastructure.command_runner import CommandRunner

class AdbNetworkService:
    @staticmethod
    def push_cert() -> bool:
        cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.cer")
        if not os.path.exists(cert_path):
            return False
        CommandRunner.run_blocking(f"adb push \"{cert_path}\" /storage/emulated/0/Download/", cwd=".")
        return True

    @staticmethod
    def route_usb():
        CommandRunner.run_blocking("adb reverse tcp:8080 tcp:8080", cwd=".")
        CommandRunner.run_blocking("adb shell settings put global http_proxy 127.0.0.1:8080", cwd=".")

    @staticmethod
    def route_wlan(ip: str):
        CommandRunner.run_blocking(f"adb shell settings put global http_proxy {ip}:8080", cwd=".")

    @staticmethod
    def reset_route():
        CommandRunner.run_blocking("adb reverse --remove-all", cwd=".")
        CommandRunner.run_blocking("adb shell settings put global http_proxy :0", cwd=".")