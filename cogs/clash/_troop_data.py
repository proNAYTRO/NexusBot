"""
Troop categories for the Nexdle trading system.

Only troops within the SAME category can be traded for each other
(this is enforced in trading_cog.py, not here).

If Clash of Clans adds new troops later, just add the name to the
right list below -- nothing else needs to change.
"""

CATEGORIES = {
    "elixir": "Elixir",
    "dark_elixir": "Dark elixir",
    "super_troop": "Super troop",
    "builder_base": "Builder base",
}

TROOPS = {
    "elixir": [
        "Archer", "Baby Dragon", "Balloon", "Barbarian", "Dragon", "Dragon Rider",
        "Electro Dragon", "Electro Titan", "Giant", "Goblin", "Healer", "Meteor Golem",
        "Miner", "P.E.K.K.A", "Root Rider", "Thrower", "Wall Breaker", "Wizard",
    ],
    "dark_elixir": [
        "Apprentice Warden", "Bowler", "Druid", "Furnace", "Golem", "Headhunter",
        "Hog Rider", "Ice Golem", "Lava Hound", "Minion", "Ruin Witch", "Valkyrie", "Witch",
    ],
    "super_troop": [
        "Ice Hound", "Inferno Dragon", "Rocket Balloon", "Sneaky Goblin", "Super Archer",
        "Super Barbarian", "Super Bowler", "Super Dragon", "Super Giant", "Super Hog Rider",
        "Super Miner", "Super Minion", "Super Valkyrie", "Super Wall Breaker", "Super Witch",
        "Super Wizard", "Super Yeti",
    ],
    "builder_base": [
        "Baby Dragon (BB)", "Beta Minion", "Boxer Giant", "Bomber", "Cannon Cart",
        "Drop Ship", "Electrofire Wizard", "Hog Glider", "Night Witch", "Power P.E.K.K.A",
        "Raged Barbarian", "Sneaky Archer",
    ],
}

ANY_TROOP = "Any troop"
