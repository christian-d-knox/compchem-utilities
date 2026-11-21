import os
import platform
import shutil
import sys
from pathlib import Path

from .console  import console, Panel
from .defaults import Defaults
from .notify   import _SendTelegram, _DetectTelegramChatID
from .prompts import AskBool, AskStr, AskChoice
from . import agent, prompts


def NotificationSetup() -> None:
    console.print(Panel("Notification Setup (Telegram)", style="operation"))
    console.print("[info]CompUtils can send you Telegram notifications when your jobs finish, and alerts the group when "
                  "someone submits a large batch of jobs to the queue using its utilities.[/info]")

    # Every early exit saves what was set so far, then closes the section
    def StopSetup(*lines: str) -> None:
        Defaults._SaveSection("notifications.toml")
        for line in lines:
            print(line)
        print("=" * 55)

    # Asked through an agent, the question says what the human does next (the run waits for their message)
    question = ("  Enable notifications?" if prompts.interactive else
                "Enable Telegram notifications for when your jobs finish? If yes: right after the next run starts, "
                "send any message to your bot in Telegram (CompUtils waits 60 seconds for it, to learn your chat ID).")
    choice = AskBool(question, "Y", key="notifications")
    if not choice:
        StopSetup("  Notifications disabled.", "  Re-run setup or edit notifications.toml to enable later.")
        return

    Defaults.isNotifications = True

    from .profile import InstalledProfile
    profile = InstalledProfile()
    botToken = profile.settings.get("botToken", "") if profile else ""
    if botToken:
        console.print("[info]  Using the bot token from your lab profile.[/info]")
    else:
        if prompts.interactive:
            print("  You'll need a bot token: your lab admin's, or your own from Telegram's @BotFather.")
            print("  (Enter 'q' to quit and come back later.)")
        botToken = AskStr("  Bot token" if prompts.interactive else
                          "Telegram bot token (your lab admin's, or your own from Telegram's @BotFather; q to skip "
                          "notifications)", key="bot-token").strip()

        if botToken.lower() == "q":
            StopSetup("  Setup paused. Access later by calling with the --first flag.")
            return
        if ":" not in botToken:
            if "bot-token" in agent.answers:
                raise agent.UsageError("--answer bot-token: that doesn't look like a bot token (expected "
                                       "123456789:ABCdef...).")
            console.print("[warning]  Warning: that doesn't look like a valid bot token[/warning]")
            print("  (expected format: 123456789:ABCdef...).")
            StopSetup("  Setup cancelled. Access later by calling with the --first flag.")
            return

    Defaults.botToken = botToken

    # A chat ID given in advance (an agent's re-run after no message came) needs no wait
    if "chat-id" in agent.answers:
        chatID = agent.answers["chat-id"] or None
    else:
        print("  Now open Telegram and send any message to the bot.")
        chatID = _DetectTelegramChatID(botToken, timeoutSeconds=60)

    if chatID:
        Defaults.chatID = chatID
        console.print(f"  \u2713 Chat ID detected: {chatID}") #light_green good

        # Unattended, the test is sent unless the answer says otherwise (its default): it confirms the token and chat
        testChoice = (AskBool("Send a test message?", "Y", key="test-message")
                      if prompts.interactive or "test-message" in agent.answers else True)
        if testChoice:
            success = _SendTelegram(botToken, chatID,
                "Welcome to remote queue notifications with CompUtils!")
            if success:
                console.print("[good]Test sent! Check Telegram.[/good]")
            else:
                console.print("[error]Test failed. Double-check the bot token.[/error]")
    else:
        if "chat-id" not in agent.answers:
            console.print("[error]Detection timed out. This usually means the token is incorrect or Telegram was "
                          "unreachable.[/error]")

        manualID = "" if "chat-id" in agent.answers else AskStr(
            "Enter your chat ID manually (or press Enter to skip)" if prompts.interactive else
            "No message reached the bot within 60 seconds. Your Telegram chat ID, if you know it (empty: no "
            "personal notifications for now; cu -first in a terminal tries again)", "", key="chat-id").strip()
        if manualID:
            Defaults.chatID = manualID
        else:
            console.print(Panel("Personal notifications won't work (group broadcast alerts still will). "
                                "Run CompUtils with --first at a later date (or edit notifications.toml directly) if "
                                "you wish to try again."), style="info")

    Defaults._SaveSection("notifications.toml")

    console.print(f"[good]Saved to {Defaults.binDirectory}/notifications.toml[/good]") #light_green good
    console.print(Panel("Reminders:\nnotifications.toml contains the bot token.\nDO NOT EDIT THIS FOR ANY REASON"
                        + ("\nJoin the lab's broadcasting group for queue alerts!" if Defaults.broadcastGroupChatID else ""),
                        style="info"))

def ColorSetup() -> None:
    console.print(Panel("Color Mode Setup", style="operation"))
    console.print("[info]CompUtils can operate in both a low color (16-color ANSI) and high color (HTML hex code) "
                  "format.\nPlease note that your terminal application must also be able to support a high color format "
                  "in addition to the set-up here.[/info]")
    console.print(Panel("For Windows users, this means high color is unavailable in PuTTY. Consider swapping "
                        "to something that supports truecolor, like Windows Terminal.", style="warning"))
    if _OldMacTerminal():
        console.print(Panel("This is macOS' Terminal, which shows high color only from macOS 26 on. Choose low color "
                            "here, or use a terminal that supports truecolor, like iTerm2.", style="warning"))
    hexColor = AskBool("Would you like to operate in high color mode?", "Y", key="color-mode")
    SetColorMode(hexColor)


def SetColorMode(hexColor: bool, shellFile: bool = True) -> None:
    """Saves the colour mode; high colour also makes sure the shell says truecolor (COLORTERM, in its startup file;
    shellFile False: not touched, for a computer whose terminal isn't a Unix shell's)."""
    Defaults.colorMode = "hexCode" if hexColor else "lowColor"
    Defaults._SaveSection("qol.toml")
    if not hexColor or not shellFile:
        return
    if os.environ.get("COLORTERM","").lower() in ("truecolor", "24bit"):
        console.print("[good]High color environment variable already detected! Enjoy![/good]")
    else:
        startup = _ShellStartupFile()
        console.print(f"[operation]High color environment variable not detected. Adding to {startup}[/operation]")
        with open(startup, "a") as file:
            file.write(f"export COLORTERM=truecolor\n")
            console.print("[good]High color environment variable written! Enjoy![/good]")


def _ShellStartupFile() -> Path:
    """Where a shell setting goes: ~/.bashrc, except on a Mac, whose terminals start login shells (zsh by default)
    that read the profile (the file conda-installer.py puts the 'cu' command on PATH in)."""
    if sys.platform != "darwin":
        return Path("~/.bashrc").expanduser()
    shell = os.path.basename(os.environ.get("SHELL", "")) or "zsh"
    return Path("~").expanduser() / (".bash_profile" if shell == "bash" else ".zprofile")


def _OldMacTerminal() -> bool:
    """Whether this is macOS' Terminal before macOS 26 (Tahoe), which has no truecolor (256 colours at most)."""
    if sys.platform != "darwin" or os.environ.get("TERM_PROGRAM") != "Apple_Terminal":
        return False
    version = platform.mac_ver()[0]
    return bool(version) and version.split(".")[0].isdigit() and int(version.split(".")[0]) < 26




def OnCluster() -> bool:
    """Whether this computer submits jobs (SLURM's sbatch is here): a cluster's login node, not a person's own
    computer (a local install: Windows, a Mac, a Linux PC)."""
    return shutil.which("sbatch") is not None


def UnattendedSetup() -> None:
    """First-time setup in a run nobody at a terminal can answer (an agent's, over ssh or on the human's own computer),
    so the human never has to run `cu` themselves first. High colour, asked of nobody. A local
    install (no sbatch here) needs nothing else: no cluster (hpcType LOCAL_INSTALL), no lab profile, no notifications.
    On a cluster, the lab profile, the cluster and notifications are firstTimeSetup's own keyed questions: each stops
    the run (exit 3) until the agent has asked the human and re-runs with the answers (--answer KEY=VALUE)."""
    from .defaults import LOCAL_INSTALL
    cluster = OnCluster()
    if Defaults.colorMode not in ("lowColor", "hexCode"):
        SetColorMode(True, shellFile=cluster)
    if not Defaults.needsFirstTimeSetup:
        return
    if not cluster:
        Defaults.hpcType = LOCAL_INSTALL
        Defaults._SaveKeys(["hpcType"])
        Defaults.isNotifications = False
        Defaults._SaveSection("notifications.toml")
        agent.Notice("info", "Set up as a local install: high color, no cluster, lab profile or notifications "
                             "(run `cu -first` in a terminal to change that).")
    elif firstTimeSetup() is None:
        raise agent.UsageError("--answer cluster=q: CompUtils needs a cluster to write job scripts for.")
    # Set up: the rest of this run goes on as any other (Main's interactive setup steps see nothing to do)
    Defaults.needsFirstTimeSetup = False


def firstTimeSetup(profileFile: Path | None = None) -> bool | None:
    """Apply a lab profile (if there is one), then set the cluster (and notifications). True once a cluster is set,
    None if the user cancelled."""
    from .profile import SetupProfile
    SetupProfile(profileFile)
    clusters = Defaults.Clusters()
    # The lab profile's clusters come first, so a labmate's cluster is the default
    index = AskChoice("Which HPC cluster are you using?", [cluster.hpcType for cluster in clusters], key="cluster")
    if index is None:
        console.print("[error]CompUtils needs a cluster to write job scripts for. For one not listed, a lab profile can "
                      "add it (see example-profile.toml).[/error]")
        return None
    cluster = clusters[index]
    Defaults.hpcType, Defaults.submissionList = cluster.hpcType, cluster.submissionList
    for key, value in cluster.settings.items():
        setattr(Defaults, key, value)
    # The cluster is saved last: it is what marks setup done, so a run stopped at a notifications question (an
    # agent's, exit 3) comes back through every question with its answers
    NotificationSetup()
    Defaults._SaveKeys(["hpcType", *cluster.settings])
    return True
