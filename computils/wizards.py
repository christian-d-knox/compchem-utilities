from .console  import console
from .defaults import Defaults, H2PSubmission, Bridges2Submission, Stampede3Submission
from .notify   import _SendTelegram, _DetectTelegramChatID
from .prompts import *

def NotificationSetup() -> None:
    print("=" * 55)
    print("  Notification Setup (Telegram)")
    print("=" * 55)
    print("\n  CompUtils can send you Telegram notifications when")
    print("  your jobs finish, and alert the group when someone submits a large batch to the queue.")

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
                "\u2713 Welcome to remote queue notifications with CompUtils!")
            if success:
                console.print("[good]  \u2713 Test sent! Check Telegram.[/good]")
            else:
                console.print("[error]  \u2717 Test failed. Double-check the bot token.[/error]")
    else:
        console.print("[error]  \u2717 Detection timed out.[/error]")
        print("  This usually means the bot token is wrong, or")
        print("  Telegram couldn't be reached from this machine.")

        manualID = AskStr("Enter your chat ID manually (or press Enter to skip)").strip()
        if manualID:
            Defaults.chatID = manualID
        else:
            print("  Personal notifications won't work, but")
            print("  broadcast alerts will still go to the group.")
            print("  Re-run setup or edit notifications.toml later.")

    Defaults._SaveSection("notifications.toml")

    console.print(f"[good]  \u2713 Saved to {Defaults.binDirectory}/notifications.toml[/good]") #light_green good
    print("  Reminders:")
    print("    \u2022 notifications.toml contains your bot token —")
    print("      DO NOT EDIT THIS FOR ANY REASON.")
    print("    \u2022 Join the lab's broadcast group for queue alerts!")
    print("=" * 55)

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