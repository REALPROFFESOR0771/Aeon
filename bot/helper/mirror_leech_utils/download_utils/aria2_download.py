from aiofiles.os import path as aiopath
from aiofiles.os import remove as aioremove

from bot import (
    LOGGER,
    aria2,
    config_dict,
    aria2_options,
    aria2c_global,
    download_dict,
    non_queued_dl,
    queue_dict_lock,
    download_dict_lock,
)
from bot.helper.ext_utils.bot_utils import sync_to_async, bt_selection_buttons
from bot.helper.ext_utils.task_manager import is_queued
from bot.helper.telegram_helper.message_utils import send_message, sendStatusMessage
from bot.helper.mirror_leech_utils.status_utils.aria2_status import Aria2Status


async def add_aria2c_download(
    link, path, listener, filename, header, ratio, seed_time
):
    a2c_opt = {**aria2_options}
    [a2c_opt.pop(k) for k in aria2c_global if k in aria2_options]
    a2c_opt["dir"] = path
    if filename:
        a2c_opt["out"] = filename
    if header:
        a2c_opt["header"] = header
    if ratio:
        a2c_opt["seed-ratio"] = ratio
    if seed_time:
        a2c_opt["seed-time"] = seed_time
    if TORRENT_TIMEOUT := config_dict["TORRENT_TIMEOUT"]:
        a2c_opt["bt-stop-timeout"] = f"{TORRENT_TIMEOUT}"
    added_to_queue, event = await is_queued(listener.uid)
    if added_to_queue:
        if link.startswith("magnet:"):
            a2c_opt["pause-metadata"] = "true"
        else:
            a2c_opt["pause"] = "true"
    try:
        download = (await sync_to_async(aria2.add, link, a2c_opt))[0]
    except Exception as e:
        LOGGER.info(f"Aria2c Download Error: {e}")
        await send_message(listener.message, f"{e}")
        return
    if await aiopath.exists(link):
        await aioremove(link)
    if download.error_message:
        error = str(download.error_message).replace("<", " ").replace(">", " ")
        LOGGER.info(f"Aria2c Download Error: {error}")
        await send_message(listener.message, error)
        return

    gid = download.gid
    name = download.name
    async with download_dict_lock:
        download_dict[listener.uid] = Aria2Status(
            gid, listener, queued=added_to_queue
        )
    if added_to_queue:
        LOGGER.info(f"Added to Queue/Download: {name}. Gid: {gid}")
        if not listener.select or not download.is_torrent:
            await sendStatusMessage(listener.message)
    else:
        async with queue_dict_lock:
            non_queued_dl.add(listener.uid)
        LOGGER.info(f"Aria2Download started: {name}. Gid: {gid}")

    await listener.on_download_start()

    if not added_to_queue and (not listener.select or not config_dict["BASE_URL"]):
        await sendStatusMessage(listener.message)
    elif listener.select and download.is_torrent and not download.is_metadata:
        if not added_to_queue:
            await sync_to_async(aria2.client.force_pause, gid)
        s_buttons = bt_selection_buttons(gid)
        msg = "Your download paused. Choose files then press Done Selecting button to start downloading."
        await send_message(listener.message, msg, s_buttons)

    if added_to_queue:
        await event.wait()

        async with download_dict_lock:
            if listener.uid not in download_dict:
                return
            download = download_dict[listener.uid]
            download.queued = False
            new_gid = download.gid()

        await sync_to_async(aria2.client.unpause, new_gid)
        LOGGER.info(f"Start Queued Download from Aria2c: {name}. Gid: {gid}")

        async with queue_dict_lock:
            non_queued_dl.add(listener.uid)