import asyncio, logging, re, string

from pushbullet import PushBullet

from hangups.ui.utils import get_conv_name

nicks = {}

def _initialise(Handlers, bot=None):
    if bot:
        _migrate_mention_config_to_memory(bot)
    Handlers.register_handler(_handle_mention, "message")
    return ["mention", "pushbulletapi", "dnd", "setnickname"]


def _migrate_mention_config_to_memory(bot):
    # migrate pushbullet apikeys to memory.json
    if bot.config.exists(["pushbullet"]):
        api_settings = bot.config.get("pushbullet")
        for user_chat_id in api_settings:
            user_api = api_settings[user_chat_id]
            print("migration(): {} = {} to memory.json".format(user_chat_id, user_api))
            bot.initialise_memory(user_chat_id, "user_data")
            bot.memory.set_by_path(["user_data", user_chat_id, "pushbullet"], user_api)
        del bot.config["pushbullet"]
        bot.memory.save()
        bot.config.save()
        print("migration(): pushbullet config migrated")

    # migrate DND list to memory.json
    if bot.config.exists(["donotdisturb"]):
        dndlist = bot.config.get("donotdisturb")
        bot.memory.set_by_path(["donotdisturb"], dndlist)
        del bot.config["donotdisturb"]
        bot.memory.save()
        bot.config.save()
        print("migration(): dnd list migrated")


@asyncio.coroutine
def _handle_mention(bot, event, command):
    """handle @mention"""
    occurrences = [word for word in event.text.split() if word.startswith('@')]
    if len(occurrences) > 0:
        for word in occurrences:
            # strip all special characters
            cleaned_name = ''.join(e for e in word if e.isalnum())
            yield from command.run(bot, event, *["mention", cleaned_name])


def _user_has_dnd(bot, user_id):
    initiator_has_dnd = False
    if bot.memory.exists(["donotdisturb"]):
        donotdisturb = bot.memory.get('donotdisturb')
        if user_id in donotdisturb:
            initiator_has_dnd = True
    return initiator_has_dnd


def mention(bot, event, *args):
    """alert a @mentioned user"""

    """minimum length check for @mention"""
    username = args[0].strip()
    if len(username) < 2:
        logging.warning("@mention from {} ({}) too short (== '{}')".format(event.user.full_name, event.user.id_.chat_id, username))
        return

    users_in_chat = event.conv.users
    mention_chat_ids = []

    """check if synced room, if so, append on the users"""
    sync_room_list = bot.get_config_suboption(event.conv_id, 'sync_rooms')
    if sync_room_list:
        if event.conv_id in sync_room_list:
            for syncedroom in sync_room_list:
                if event.conv_id not in syncedroom:
                    users_in_chat += bot.get_users_in_conversation(syncedroom)
            users_in_chat = list(set(users_in_chat)) # make unique

    """
    /bot mention <fragment> test
    """
    noisy_mention_test = False
    if len(args) == 2 and args[1] == "test":
        noisy_mention_test = True

    initiator_has_dnd = _user_has_dnd(bot, event.user.id_.chat_id)

    """
    quidproquo: users can only @mention if they themselves are @mentionable (i.e. have a 1-on-1 with the bot)
    """
    conv_1on1_initiator = bot.get_1on1_conversation(event.user.id_.chat_id)
    if bot.get_config_option("mentionquidproquo"):
        if conv_1on1_initiator:
            if initiator_has_dnd:
                logging.info("quidproquo: user {} ({}) has DND active".format(event.user.full_name, event.user.id_.chat_id))
                if noisy_mention_test or bot.get_config_suboption(event.conv_id, 'mentionerrors'):
                    bot.send_message_parsed(
                        event.conv,
                        "<b>{}</b>, you cannot @mention anyone until your DND status is toggled off.".format(
                            event.user.full_name))
                return
            else:
                logging.info("quidproquo: user {} ({}) has 1-on-1".format(event.user.full_name, event.user.id_.chat_id))
        else:
            logging.warning("quidproquo: user {} ({}) has no 1-on-1".format(event.user.full_name, event.user.id_.chat_id))
            if noisy_mention_test or bot.get_config_suboption(event.conv_id, 'mentionerrors'):
                bot.send_message_parsed(
                    event.conv,
                    "<b>{}</b> cannot @mention anyone until they say something to me first.".format(
                        event.user.full_name))
            return

    """track mention statistics"""
    user_tracking = {
      "mentioned":[],
      "ignored":[],
      "failed": {
        "pushbullet": [],
        "one2one": [],
      }
    }

    """
    begin mentioning users as long as they exist in the current conversation...
    """

    conversation_name = get_conv_name(event.conv, truncate=True);
    logging.info("@mention '{}' in '{}' ({})".format(username, conversation_name, event.conv.id_))
    username_lower = username.lower()

    """is @all available globally/per-conversation/initiator?"""
    if username_lower == "all":
        if not bot.get_config_suboption(event.conv.id_, 'mentionall'):

            """global toggle is off/not set, check admins"""
            logging.info("@all in {}: disabled/unset global/per-conversation".format(event.conv.id_))
            admins_list = bot.get_config_suboption(event.conv_id, 'admins')
            if event.user_id.chat_id not in admins_list:

                """initiator is not an admin, check whitelist"""
                logging.info("@all in {}: user {} ({}) is not admin".format(event.conv.id_, event.user.full_name, event.user.id_.chat_id))
                all_whitelist = bot.get_config_suboption(event.conv_id, 'mentionallwhitelist')
                if all_whitelist is None or event.user_id.chat_id not in all_whitelist:

                    logging.warning("@all in {}: user {} ({}) blocked".format(event.conv.id_, event.user.full_name, event.user.id_.chat_id))
                    if conv_1on1_initiator:
                        bot.send_message_parsed(
                            conv_1on1_initiator,
                            "You are not allowed to use @all in <b>{}</b>".format(
                                conversation_name))
                    if noisy_mention_test or bot.get_config_suboption(event.conv_id, 'mentionerrors'):
                        bot.send_message_parsed(
                            event.conv,
                            "<b>{}</b> blocked from using <i>@all</i>".format(
                                event.user.full_name))
                    return
                else:
                    logging.info("@all in {}: allowed, {} ({}) is whitelisted".format(event.conv.id_, event.user.full_name, event.user.id_.chat_id))
            else:
                logging.info("@all in {}: allowed, {} ({}) is an admin".format(event.conv.id_, event.user.full_name, event.user.id_.chat_id))
        else:
            logging.info("@all in {}: enabled global/per-conversation".format(event.conv.id_))

    """generate a list of users to be @mentioned"""
    exact_nickname_matches = []
    mention_list = []
    for u in users_in_chat:

        # mentions also checks nicknames if one is configured
        #  exact matches only! see following IF block
        nickname = ""
        nickname_lower = ""
        if bot.memory.exists(['user_data', u.id_.chat_id, "nickname"]):
            nickname = bot.memory.get_by_path(['user_data', u.id_.chat_id, "nickname"])
            nickname_lower = nickname.lower()

        if username_lower == "all" or \
                username_lower in u.full_name.replace(" ", "").lower() or \
                username_lower in u.full_name.replace(" ", "_").lower() or \
                username_lower == nickname_lower:

            logging.info("user {} ({}) is present".format(u.full_name, u.id_.chat_id))

            if u.is_self:
                """bot cannot be @mentioned"""
                logging.info("suppressing bot mention by {} ({})".format(event.user.full_name, event.user.id_.chat_id))
                continue

            if u.id_.chat_id == event.user.id_.chat_id and username_lower == "all":
                """prevent initiating user from receiving duplicate @all"""
                logging.info("suppressing @all for {} ({})".format(event.user.full_name, event.user.id_.chat_id))
                continue

            if u.id_.chat_id in mention_chat_ids:
                """prevent most duplicate mentions (in the case of syncouts)"""
                logging.info("suppressing duplicate mention for {} ({})".format(event.user.full_name, event.user.id_.chat_id))
                continue

            if bot.memory.exists(["donotdisturb"]):
                if _user_has_dnd(bot, u.id_.chat_id):
                    logging.info("suppressing @mention for {} ({})".format(u.full_name, u.id_.chat_id))
                    user_tracking["ignored"].append(u.full_name)
                    continue

            if username_lower == nickname_lower:
                if u not in exact_nickname_matches:
                    exact_nickname_matches.append(u)

            if u not in mention_list:
                mention_list.append(u)

    """prioritise exact nickname matches"""
    if len(exact_nickname_matches) == 1:
        logging.info("prioritising nickname match for {}".format(exact_nickname_matches[0].full_name))
        mention_list = exact_nickname_matches

    if len(mention_list) > 1 and username_lower != "all":
        if conv_1on1_initiator:
            text_html = '{} users would be mentioned with "@{}"! Be more specific. List of matching users:<br />'.format(
                len(mention_list), username, conversation_name)

            for u in mention_list:
                text_html += u.full_name
                if bot.memory.exists(['user_data', u.id_.chat_id, "nickname"]):
                    text_html += ' (' + bot.memory.get_by_path(['user_data', u.id_.chat_id, "nickname"]) + ')'
                text_html += '<br />'

            bot.send_message_parsed(conv_1on1_initiator, text_html)

        logging.info("@{} not sent due to multiple recipients".format(username_lower))
        return #SHORT-CIRCUIT

    """send @mention alerts"""
    for u in mention_list:
            alert_via_1on1 = True

            """pushbullet integration"""
            if bot.memory.exists(['user_data', u.id_.chat_id, "pushbullet"]):
                pushbullet_config = bot.memory.get_by_path(['user_data', u.id_.chat_id, "pushbullet"])
                if pushbullet_config is not None:
                    if pushbullet_config["api"] is not None:
                        pb = PushBullet(pushbullet_config["api"])
                        success, push = pb.push_note(
                            "{} mentioned you in {}".format(
                                    event.user.full_name,
                                    conversation_name),
                                event.text)
                        if success:
                            user_tracking["mentioned"].append(u.full_name)
                            logging.info("{} ({}) alerted via pushbullet".format(u.full_name, u.id_.chat_id))
                            alert_via_1on1 = False # disable 1on1 alert
                        else:
                            user_tracking["failed"]["pushbullet"].append(u.full_name)
                            logging.warning("pushbullet alert failed for {} ({})".format(u.full_name, u.id_.chat_id))

            if alert_via_1on1:
                """send alert with 1on1 conversation"""
                conv_1on1 = bot.get_1on1_conversation(u.id_.chat_id)
                if conv_1on1:
                    bot.send_message_parsed(
                        conv_1on1,
                        "<b>{}</b> @mentioned you in <i>{}</i>:<br />{}".format(
                            event.user.full_name,
                            conversation_name,
                            event.text)) # prevent internal parser from removing <tags>
                    mention_chat_ids.append(u.id_.chat_id)
                    user_tracking["mentioned"].append(u.full_name)
                    logging.info("{} ({}) alerted via 1on1 ({})".format(u.full_name, u.id_.chat_id, conv_1on1.id_))
                else:
                    user_tracking["failed"]["one2one"].append(u.full_name)
                    if bot.get_config_suboption(event.conv_id, 'mentionerrors'):
                        bot.send_message_parsed(
                            event.conv,
                            "@mention didn't work for <b>{}</b>. User must say something to me first.".format(
                                u.full_name))
                    logging.warning("user {} ({}) could not be alerted via 1on1".format(u.full_name, u.id_.chat_id))

    if noisy_mention_test:
        text_html = "<b>@mentions:</b><br />"
        if len(user_tracking["failed"]["one2one"]) > 0:
            text_html = text_html + "1-to-1 fail: <i>{}</i><br />".format(", ".join(user_tracking["failed"]["one2one"]))
        if len(user_tracking["failed"]["pushbullet"]) > 0:
            text_html = text_html + "PushBullet fail: <i>{}</i><br />".format(", ".join(user_tracking["failed"]["pushbullet"]))
        if len(user_tracking["ignored"]) > 0:
            text_html = text_html + "Ignored (DND): <i>{}</i><br />".format(", ".join(user_tracking["ignored"]))
        if len(user_tracking["mentioned"]) > 0:
            text_html = text_html + "Alerted: <i>{}</i><br />".format(", ".join(user_tracking["mentioned"]))
        else:
            text_html = text_html + "Nobody was successfully @mentioned ;-(<br />"

        if len(user_tracking["failed"]["one2one"]) > 0:
            text_html = text_html + "Users failing 1-to-1 need to say something to me privately first.<br />"

        bot.send_message_parsed(event.conv, text_html)

def pushbulletapi(bot, event, *args):
    """allow users to configure pushbullet integration with api key
        /bot pushbulletapi [<api key>|false, 0, -1]"""

    # XXX: /bot config exposes all configured api keys (security risk!)

    if len(args) == 1:
        value = args[0]
        if value.lower() in ('false', '0', '-1'):
            value = None
            bot.send_message_parsed(
                event.conv,
                "deactivating pushbullet integration")
        else:
            bot.send_message_parsed(
                event.conv,
                "setting pushbullet api key")

        bot.initialise_memory(event.user.id_.chat_id, "user_data")
        bot.memory.set_by_path(["user_data", event.user.id_.chat_id, "pushbullet"], { "api": value })
        bot.memory.save()
    else:
        bot.send_message_parsed(
            event.conv,
            "pushbullet configuration not changed")


def dnd(bot, event, *args):
    """allow users to toggle DND for ALL conversations (i.e. no @mentions)
        /bot dnd"""

    # ensure dndlist is initialised
    if not bot.memory.exists(["donotdisturb"]):
        bot.memory["donotdisturb"] = []

    initiator_chat_id = event.user.id_.chat_id
    dnd_list = bot.memory.get("donotdisturb")
    if initiator_chat_id in dnd_list:
        dnd_list.remove(initiator_chat_id)
        bot.send_message_parsed(
            event.conv,
            "global DND toggled OFF for {}".format(event.user.full_name))
    else:
        dnd_list.append(initiator_chat_id)
        bot.send_message_parsed(
            event.conv,
            "global DND toggled ON for {}".format(event.user.full_name))

    bot.memory["donotdisturb"] = dnd_list
    bot.memory.save()

def setnickname(bot, event, *args):
    """allow users to set a nickname for sync relay
        /bot setnickname <nickname>"""

    truncatelength = 16 # What should the maximum length of the nickname be?
    minlength = 2 # What should the minimum length of the nickname be?

    nickname = ' '.join(args).strip()

    # Strip all non-alphanumeric characters
    nickname = re.sub('[^0-9a-zA-Z-_]+', '', nickname)

    # Truncate nickname
    nickname = nickname[0:truncatelength]

    if len(nickname) < minlength and not nickname == '': # Check minimum length
        bot.send_message_parsed(event.conv, "Error: Minimum length of nickname is {} characters. Only alphabetical and numeric characters allowed.".format(minlength))
        return

    # perform hard-coded substitution on words that trigger easter eggs
    #   dammit google! ;P
    substitution = {
        "woot": "w00t",
        "woohoo": "w00h00",
        "lmao": "lma0",
        "rofl": "r0fl",

        "hahahaha": "ha_ha_ha_ha",
        "hehehehe": "he_he_he_he",
        "jejejeje": "je_je_je_je",
        "rsrsrsrs": "rs_rs_rs_rs",

        "happy birthday": "happy_birthday",
        "happy new year": "happy_new_year",

        "xd": "x_d"
    }
    for original in substitution:
        if original in nickname.lower():
            pattern = re.compile(original, re.IGNORECASE)
            nickname = pattern.sub(substitution[original], nickname)

    # Prevent duplicate nicknames
    # First, populate list of nicks if not already
    if not nicks:
        for userchatid in bot.memory.get_option("user_data"):
            usernick = bot.memory.get_suboption("user_data", userchatid, "nickname")
            if usernick:
                nicks[userchatid] = usernick.lower()

    # is the user trying to re-set his own nickname? - don't do anything if that is the case
    if event.user.id_.chat_id in nicks:
        if nickname.lower() == nicks[event.user.id_.chat_id].lower():
            actual_nickname = bot.memory.get_suboption("user_data", event.user.id_.chat_id, "nickname")
            bot.send_message_parsed(event.conv, '<i>Your nickname is already "' + actual_nickname + '".</i>')
            return

    # check whether another user has the same nickname
    if nickname.lower() in nicks.values():
        bot.send_message_parsed(event.conv, '<i>Nickname "' + nickname + '" is already in use by another user.</i>')
        return

    bot.initialise_memory(event.user.id_.chat_id, "user_data")

    bot.memory.set_by_path(["user_data", event.user.id_.chat_id, "nickname"], nickname)

    # Update nicks cache with new nickname
    nicks[event.user.id_.chat_id] = nickname

    try:
        label = '{0} ({1})'.format(event.user.full_name.split(' ', 1)[0], nickname)
    except TypeError:
        label = event.user.full_name
    bot.memory.set_by_path(["user_data", event.user.id_.chat_id, "label"], label)

    bot.memory.save()

    if(nickname == ''):
        bot.send_message_parsed(event.conv, "Removing nickname")
    else:
        bot.send_message_parsed(
            event.conv,
            "Setting nickname to '{}'".format(nickname))
