"""
Maps each troop display name (as used in _troop_data.py) to its
application emoji on the Nexus bot.

Format Discord expects for a custom emoji in text/embeds: <:name:id>
"""

TROOP_EMOJI_ID = {
    # elixir
    "Archer": ("archer", 1536347129445097533),
    "Baby Dragon": ("baby_dragon", 1536347142170349661),
    "Balloon": ("balloon", 1536347148717662228),
    "Barbarian": ("barbarian", 1536347155227217991),
    "Dragon": ("dragon", 1536347206070702091),
    "Dragon Rider": ("dragon_rider", 1536347200026837042),
    "Electro Dragon": ("electro_dragon", 1536347225037217937),
    "Electro Titan": ("electro_titan", 1536347231341256745),
    "Giant": ("giant", 1536347244763025448),
    "Goblin": ("goblin", 1536347251067330712),
    "Healer": ("healer", 1536347270449078283),
    "Meteor Golem": ("Meteor_Golem", 1536347317161037834),
    "Miner": ("miner", 1536347323515281451),
    "P.E.K.K.A": ("p_e_k_k_a", 1536347342989426708),
    "Root Rider": ("root_rider", 1536347369828909126),
    "Thrower": ("Thrower", 1536347489483886713),
    "Wall Breaker": ("wall_breaker", 1536347502004019275),
    "Wizard": ("wizard", 1536347515350286386),

    # dark elixir
    "Apprentice Warden": ("apprentice_warden", 1536347123153510420),
    "Bowler": ("bowler", 1536347180925980692),
    "Druid": ("druid", 1536347218926248037),
    "Furnace": ("Furnace", 1536347237985034372),
    "Golem": ("golem", 1536347257857900544),
    "Headhunter": ("headhunter", 1536347264275193876),
    "Hog Rider": ("hog_rider", 1536347283497549864),
    "Ice Golem": ("ice_golem", 1536347289512050830),
    "Lava Hound": ("lava_hound", 1536347310479638628),
    "Minion": ("minion", 1536347330146467931),
    "Ruin Witch": ("ruin_witch", 1536347380142706688),
    "Valkyrie": ("valkyrie", 1536347495871815742),
    "Witch": ("witch", 1536347508836536330),

    # super troops
    "Ice Hound": ("ice_hound", 1536347296520740874),
    "Inferno Dragon": ("inferno_dragon", 1536347303160455210),
    "Rocket Balloon": ("rocket_balloon", 1536347363394723904),
    "Sneaky Goblin": ("sneaky_goblin", 1536347393111494756),
    "Super Archer": ("super_archer", 1536347408454394017),
    "Super Barbarian": ("super_barbarian", 1536347415026864158),
    "Super Bowler": ("super_bowler", 1536347421741682822),
    "Super Dragon": ("super_dragon", 1536347428771336283),
    "Super Giant": ("super_giant", 1536347435289411584),
    "Super Hog Rider": ("super_hog_rider", 1536347441765556324),
    "Super Miner": ("super_miner", 1536347448572911616),
    "Super Minion": ("super_minion", 1536347455162163331),
    "Super Valkyrie": ("super_valkyrie", 1536347462023913536),
    "Super Wall Breaker": ("super_wall_breaker", 1536347469124735077),
    "Super Witch": ("super_witch", 1536347475638485083),
    "Super Wizard": ("super_wizard", 1536347482605355158),
    "Super Yeti": ("Super_Yeti", 1536347400275501056),

    # builder base
    "Baby Dragon (BB)": ("baby_dragon_bb", 1536347135690285116),
    "Beta Minion": ("beta_minion", 1536347168019980329),
    "Boxer Giant": ("boxer_giant", 1536347187309445160),
    "Bomber": ("bomber", 1536347174462300250),
    "Cannon Cart": ("cannon_cart", 1536347193596711062),
    "Drop Ship": ("drop_ship", 1536347212446048257),
    "Electrofire Wizard": ("BB_Electrofire_Wizard", 1536347161640439868),
    "Hog Glider": ("hog_glider", 1536347277138989056),
    "Night Witch": ("night_witch", 1536347336413028494),
    "Power P.E.K.K.A": ("power_p_e_k_k_a", 1536347349478150184),
    "Raged Barbarian": ("raged_barbarian", 1536347355912212520),
    "Sneaky Archer": ("sneaky_archer", 1536347386358792304),
}


def troop_emoji_str(troop_name: str) -> str:
    """Return '<:name:id>' text form for use in embed descriptions/messages.
    Falls back to '' if the troop has no emoji (e.g. 'Any troop')."""
    entry = TROOP_EMOJI_ID.get(troop_name)
    if not entry:
        return ""
    name, emoji_id = entry
    return f"<:{name}:{emoji_id}>"


def troop_emoji_partial(troop_name: str):
    """Return a discord.PartialEmoji for use in SelectOption(emoji=...).
    Returns None if the troop has no emoji."""
    import discord
    entry = TROOP_EMOJI_ID.get(troop_name)
    if not entry:
        return None
    name, emoji_id = entry
    return discord.PartialEmoji(name=name, id=emoji_id)
