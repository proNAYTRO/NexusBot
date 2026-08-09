"""Application emoji IDs for KreweNexus's bot-wide emoji pack.

Uploaded once via `utils/upload_emojis.py` as *application* emojis (not guild
emojis), so every ID below renders in any server the bot is in. Re-running
the uploader against the same folder is safe (it skips names that already
exist) but will NOT change these IDs, so this file stays accurate unless an
emoji is deleted and re-created from scratch.
"""

EMOJI_IDS = {
    # ── nex_* UI pack ──────────────────────────────────────────────────────
    "nex_ban": 1525622769713414234,
    "nex_bar_lstart_empty": 1525622775308357825,
    "nex_bar_lstart_full": 1525622780714811462,
    "nex_bar_mid_empty": 1525622786952003756,
    "nex_bar_mid_full": 1525622792886947941,
    "nex_bar_rend_empty": 1525622798054064188,
    "nex_bar_rend_full": 1525622803515052123,
    "nex_bell": 1525622809399787671,
    "nex_blank": 1525622815032737803,
    "nex_boost": 1525622820598452264,
    "nex_branch": 1525622825531080736,
    "nex_bullet": 1525622831013040261,
    "nex_clash": 1525811690174808136,
    "nex_close": 1525622841611911288,
    "nex_confirm": 1525622846951522495,
    "nex_delete": 1525622852550791289,
    "nex_divider": 1525622857902592190,
    "nex_divider_rw": 1525622862994739313,
    "nex_divider_wr": 1525622868384284735,
    "nex_dnd": 1525622873350344765,
    "nex_down": 1525622878895341569,
    "nex_dropdown": 1525622884138221677,
    "nex_error": 1525622889523576988,
    "nex_expand": 1525622894854537367,
    "nex_filter": 1525622900311195688,
    "nex_first": 1525622905642156173,
    "nex_home": 1525622910868525117,
    "nex_idle": 1525622916094492672,
    "nex_info": 1525622921362669578,
    "nex_last": 1525622926924185640,
    "nex_loading": 1525624427621122058,
    "nex_locked": 1525622937401688084,
    "nex_member": 1525627749270683850,
    "nex_menu": 1525622942405230717,
    "nex_mute": 1525622948101226506,
    "nex_next": 1525622953486585916,
    "nex_offline": 1525622958821740734,
    "nex_online": 1525622964090048612,
    "nex_ping": 1525624421136728158,
    "nex_prev": 1525622974546186311,
    "nex_refresh": 1525622979575414989,
    "nex_reply": 1525622985065496738,
    "nex_search": 1525622990275084430,
    "nex_settings": 1525622995601719436,
    "nex_success": 1525623000580489438,
    "nex_timeout": 1525623006032957542,
    "nex_trophy": 1525808789767454812,
    "nex_unlocked": 1525623016078180513,
    "nex_up": 1525623021765660805,
    "nex_verified": 1525623028657160364,
    "nex_warning": 1525623033568563516,
    "nex_xp": 1525623039214223522,

    # ── Hero icons ─────────────────────────────────────────────────────────
    "BK": 1525612358796447944,
    "AQ": 1525612353474002984,
    "GW": 1525612369227550730,
    "RC": 1525615619427401868,
    "DD": 1525612363997384844,
    "MP": 1525612374093070540,

    # ── Town Hall levels ───────────────────────────────────────────────────
    "TH8": 1525612428749177003,
    "TH9": 1525612434067554385,
    "TH10": 1525612379612905502,
    "TH11": 1525612385107447948,
    "TH12": 1525612390417301615,
    "TH13": 1525612396230475837,
    "TH14": 1525612401628811335,
    "TH15": 1525612407085465741,
    "TH16": 1525612412231745567,
    "TH17": 1525612417596522567,
    "TH18": 1525612423300513843,

    # ── War league ranks ───────────────────────────────────────────────────
    "Bronze_1": 1525614902872510515,
    "Bronze_2": 1525614907876442464,
    "Bronze_3": 1525614913505202276,
    "Silver_1": 1525614994430103694,
    "Silver_2": 1525614999970775214,
    "Silver_3": 1525615005146419263,
    "Gold_1": 1525614957058592919,
    "Gold_2": 1525614962301604001,
    "Gold_3": 1525614967271854363,
    "Crystal_1": 1525614935801991269,
    "Crystal_2": 1525614941338341497,
    "Crystal_3": 1525614946316980327,
    "Master_1": 1525614978218856478,
    "Master_2": 1525614983403147437,
    "Master_3": 1525614988910395563,
    "Champ_1": 1525614918760534167,
    "Champ_2": 1525614924079042702,
    "Champ_3": 1525614929372250143,
    "Titan_1": 1525615010712260658,
    "Titan_2": 1525615016252936263,
    "Titan_3": 1525615021680230641,
    "Legend_L": 1525614972972044328,

    # ── Branding / misc ────────────────────────────────────────────────────
    "nn_nexus": 1525615501969981482,
    "fdlcircle": 1525616179924832437,
}

# Emojis uploaded as .gif source files — need the "a" animated prefix in the mention string.
ANIMATED = {"nex_loading", "nex_ping"}


def emoji(name: str) -> str:
    """Return the `<:name:id>` (or `<a:name:id>` if animated) mention string for an application emoji.

    Usage: f"{emoji('nex_success')} Done!"
    """
    eid = EMOJI_IDS[name]
    prefix = "a" if name in ANIMATED else ""
    return f"<{prefix}:{name}:{eid}>"
