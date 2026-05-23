import os
from pathlib import Path

from rich.panel import Panel

from .console  import console
from .defaults import Defaults, H2PSubmission, Bridges2Submission, Stampede3Submission
from .notify   import _SendTelegram, _DetectTelegramChatID
from .prompts import *

def NotificationSetup() -> None:
    console.print(Panel("Notification Setup (Telegram)", style="operation"))
    console.print("[info]CompUtils can send you Telegram notifications when your jobs finish, and alerts the group when "
                  "someone submits a large batch of jobs to the queue using its utilities.[/info]")

    choice = AskBool("  Enable notifications?", "Y")
    if not choice:
        Defaults._SaveSection("notifications.toml")
        print("  Notifications disabled.")
        print("  Re-run setup or edit notifications.toml to enable later.")
        print("=" * 55)
        return

    Defaults.isNotifications = True

    print("  You'll need the bot token from your lab admin.")
    print("  (Enter 'q' to quit and come back later.)")
    botToken = input("  Bot token: ").strip()

    if botToken.lower() == "q":
        Defaults._SaveSection("notifications.toml")
        print("  Setup paused. Access later by calling with the --first flag.")
        print("=" * 55)
        return
    if ":" not in botToken:
        console.print("[warning]  Warning: that doesn't look like a valid bot token[/warning]")
        print("  (expected format: 123456789:ABCdef...).")
        Defaults._SaveSection("notifications.toml")
        print("  Setup cancelled. Access later by calling with the --first flag.")
        print("=" * 55)
        return

    Defaults.botToken = botToken

    print("  Now open Telegram and send any message to the bot.")
    chatID = _DetectTelegramChatID(botToken, timeoutSeconds=60)

    if chatID:
        Defaults.chatID = chatID
        console.print(f"  \u2713 Chat ID detected: {chatID}") #light_green good

        testChoice = AskBool("Send a test message?", "Y")
        if testChoice:
            success = _SendTelegram(botToken, chatID,
                "Welcome to remote queue notifications with CompUtils!")
            if success:
                console.print("[good]Test sent! Check Telegram.[/good]")
            else:
                console.print("[error]Test failed. Double-check the bot token.[/error]")
    else:
        console.print("[error]Detection timed out. This usually means the token is incorrect or Telegram was "
                      "unreachable.[/error]")

        manualID = AskStr("Enter your chat ID manually (or press Enter to skip)").strip()
        if manualID:
            Defaults.chatID = manualID
        else:
            console.print(Panel("Personal notifications won't work, but broadcast alerts will still be sent to the group"
                                ". Run CompUtils with --first at a later date (or edit notifications.toml directly) if "
                                "you wish to try again."), style="info")

    Defaults._SaveSection("notifications.toml")

    console.print(f"[good]Saved to {Defaults.binDirectory}/notifications.toml[/good]") #light_green good
    console.print(Panel("Reminders:\nnotifications.toml contains the bot token.\nDO NOT EDIT THIS FOR ANY "
                        "REASON\nJoin the lab's broadcasting group for queue alerts!", style="info"))

def ColorSetup() -> None:
    console.print(Panel("Color Mode Setup", style="operation"))
    console.print("[info]CompUtils can operate in both a low color (16-color ANSI) and high color (HTML hex code) "
                  "format.\nPlease note that your terminal application must also be able to support a high color format "
                  "in addition to the set-up here.[/info]")
    console.print(Panel("For Windows users, this means high color is unavailable in PuTTY. Consider swapping "
                        "to something that supports truecolor, like Windows Terminal.", style="warning"))
    hexColor = AskBool("Would you like to operate in high color mode?", "Y")
    if  not hexColor:
        Defaults.colorMode = "lowColor"
        Defaults._SaveSection("qol.toml")
        return

    Defaults.colorMode = "hexCode"
    Defaults._SaveSection("qol.toml")
    if os.environ.get("COLORTERM","").lower() in ("truecolor", "24bit"):
        console.print("[good]High color environment variable already detected! Enjoy![/good]")
    else:
        console.print("[operation]High color environment variable not detected. Adding to bashrc[/operation]")
        bashPath = Path("~/.bashrc").expanduser()
        with open(bashPath, "a") as file:
            file.write(f"export COLORTERM=truecolor\n")
            console.print("[good]High color environment variable written! Enjoy![/good]")




def firstTimeSetup() -> None:
    systemType = AskStr("Enter the name of the HPC cluster you are using (H2P, Expanse, Bridges2, Stampede3)")
    match systemType:
        case "H2P":
            Defaults.hpcType, Defaults.partition, Defaults.cluster = "H2P", "pliu", "smp"
            Defaults.submissionList = H2PSubmission.submissionList
        case "Bridges2":
            Defaults.hpcType, Defaults.partition = "Bridges2", "RM-shared"
            Defaults.submissionList = Bridges2Submission.submissionList
            Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 2, 0, 2
        case "Expanse":
            console.print("[error]CompUtils is not supported on the Expanse architecture due to being outdated and messy. Have a good day.[/error]")
        case "Stampede3":
            Defaults.hpcType, Defaults.partition = "Stampede3", "icx"
            Defaults.CPU, Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 80, 200/80, 0, 200/80
            Defaults.submissionList = Stampede3Submission.submissionList
        case _:
            console.print("[error]Unknown HPC architecture input. Aborting.[/error]")
            return
    Defaults._SaveSection("slurm.toml")
    NotificationSetup()
    ColorSetup()