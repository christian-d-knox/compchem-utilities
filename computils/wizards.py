from .console  import console
from .defaults import Defaults, H2PSubmission, Bridges2Submission, Stampede3Submission
from .notify   import _SendTelegram, _DetectTelegramChatID

def NotificationSetup() -> None:
    print("\n" + "=" * 55)
    print("  Notification Setup (Telegram)")
    print("=" * 55)
    print("\n  CompUtils can send you Telegram notifications when")
    print("  your jobs finish, and alert the group when someone submits a large batch to the queue.")

    while True:
        choice = input("\n  Enable notifications? [Y/n]: ").strip().lower()
        if choice in ("", "y", "n"):
            break
        print("  Please enter Y or N.")
    if choice == "n":
        Defaults._SaveSection("notifications.toml")
        print("\n  Notifications disabled.")
        print("  Re-run setup or edit notifications.toml to enable later.")
        print("=" * 55)
        return

    Defaults.isNotifications = True

    print("\n  You'll need the bot token from your lab admin.")
    print("  (Enter 'q' to quit and come back later.)")
    botToken = input("\n  Bot token: ").strip()

    if botToken.lower() == "q":
        Defaults._SaveSection("notifications.toml")
        print("\n  Setup paused. Run again when you have the token.")
        print("=" * 55)
        return
    if ":" not in botToken:
        console.print("\n  Warning: that doesn't look like a valid bot token") #light_yellow warning
        print("  (expected format: 123456789:ABCdef...).")
        while True:
            proceed = input("  Continue anyway? [y/N]: ").strip().lower()
            if proceed in ("", "n"):
                Defaults._SaveSection("notifications.toml")
                print("  Setup cancelled.")
                print("=" * 55)
                return
            if proceed == "y":
                break
            print("  Please enter Y or N.")

    Defaults.botToken = botToken


    print("\n  Now open Telegram and send any message to the bot.")
    chatID = _DetectTelegramChatID(botToken, timeoutSeconds=60)

    if chatID:
        Defaults.chatID = chatID
        console.print(f"\n  \u2713 Chat ID detected: {chatID}") #light_green good

        testChoice = input("\n  Send a test message? [Y/n]: ").strip().lower()
        if testChoice != "n":
            success = _SendTelegram(botToken, chatID,
                "\u2713 Welcome to remote queue notifications with CompUtils!")
            if success:
                console.print("  \u2713 Test sent! Check Telegram.") #light_green good
            else:
                console.print("  \u2717 Test failed. Double-check the bot token.") #light_red error
    else:
        console.print("\n  \u2717 Detection timed out.") #light_red error
        print("  This usually means the bot token is wrong, or")
        print("  Telegram couldn't be reached from this machine.")

        manualID = input("\n  Enter your chat ID manually (or press Enter to skip): ").strip()
        if manualID:
            Defaults.chatID = manualID
        else:
            print("  Personal notifications won't work, but")
            print("  broadcast alerts will still go to the group.")
            print("  Re-run setup or edit notifications.toml later.")

    Defaults._SaveSection("notifications.toml")

    console.print(f"\n  \u2713 Saved to {Defaults.binDirectory}/notifications.toml") #light_green good
    print("\n  Reminders:")
    print("    \u2022 notifications.toml contains your bot token —")
    print("      DO NOT EDIT THIS FOR ANY REASON.")
    print("    \u2022 Join the lab's broadcast group for queue alerts!")
    print("\n" + "=" * 55)

def firstTimeSetup() -> None:
    systemType = str(input("Enter the name of the HPC cluster you are using (H2P, Expanse, Bridges2, Stampede3) :"))
    match systemType:
        case "H2P":
            Defaults.hpcType, Defaults.partition, Defaults.cluster = "H2P", "pliu", "smp"
            Defaults.submissionList = H2PSubmission.submissionList
        case "Bridges2":
            Defaults.hpcType, Defaults.partition = "Bridges2", "RM-shared"
            Defaults.submissionList = Bridges2Submission.submissionList
            Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 2, 0, 2
        case "Expanse":
            console.print("CompUtils is not supported on the Expanse architecture due to being outdated and messy. Have a good day.") #light_red error
        case "Stampede3":
            Defaults.hpcType, Defaults.partition = "Stampede3", "icx"
            Defaults.CPU, Defaults.memoryRatio, Defaults.memoryBuffer, Defaults.highMemoryRatio = 80, 200/80, 0, 200/80
            Defaults.submissionList = Stampede3Submission.submissionList
        case _:
            console.print("Unknown HPC architecture input. Aborting.") #light_red error
            return
    Defaults._SaveSection("slurm.toml")
    NotificationSetup()