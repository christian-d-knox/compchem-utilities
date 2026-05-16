import json, time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .console  import console
from .defaults import Defaults

def _SendTelegram(botToken: str, chatID: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{botToken}/sendMessage"
    payload = json.dumps({
        "chat_id": chatID,
        "text": message,
        "parse_mode": "HTML"
    }).encode("utf-8")

    request = Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status == 200
    except (URLError, HTTPError, TimeoutError) as error:
        console.print(f"  [Notification] Telegram send failed: {error}") #light_red error
        return False


def _DetectTelegramChatID(botToken: str, timeoutSeconds: int = 60) -> str | None:
    baseURL = f"https://api.telegram.org/bot{botToken}"
    try:
        clearURL = f"{baseURL}/getUpdates?offset=-1"
        with urlopen(clearURL, timeout=10) as response:
            data = json.loads(response.read())
            if data.get("result"):
                lastUpdateID = data["result"][-1]["update_id"]
                ackURL = (
                    f"{baseURL}/getUpdates"
                    f"?offset={lastUpdateID + 1}"
                )
                urlopen(ackURL, timeout=10)
    except Exception:
        pass

    print("\n  Waiting for your message (60 seconds)...")
    startTime = time.time()

    while time.time() - startTime < timeoutSeconds:
        try:
            pollURL = f"{baseURL}/getUpdates?timeout=5"
            with urlopen(pollURL, timeout=15) as response:
                data = json.loads(response.read())
                for update in data.get("result", []):
                    chatID = (
                        update
                        .get("message", {})
                        .get("chat", {})
                        .get("id")
                    )
                    if chatID:
                        return str(chatID)
        except Exception:
            time.sleep(2)

    return None


def NotifyPersonal(message: str) -> None:
    if not Defaults.isNotifications:
        return
    if Defaults.botToken and Defaults.chatID:
        _SendTelegram(Defaults.botToken, Defaults.chatID, message)


def NotifyBroadcast(message: str) -> None:
    if not Defaults.isNotifications:
        return
    if Defaults.botToken and Defaults.broadcastGroupChatID:
        _SendTelegram(
            Defaults.botToken,
            Defaults.broadcastGroupChatID,
            f"\U0001F4E2 Queue Alert\n{message}"
        )


def CheckAndBroadcast(jobCount: int) -> None:
    if not Defaults.isNotifications:
        return
    if jobCount >= Defaults.broadcastThreshold:
        NotifyBroadcast(
            f"{jobCount} jobs were just submitted to the queue. "
            f"Expect slower turnaround for a while."
        )