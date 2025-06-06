from __future__ import annotations
import sys
from collections.abc import Callable, Iterable
from enum import IntEnum
from typing import TYPE_CHECKING, Optional, Any

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    TypeAlias = str

if TYPE_CHECKING:
    from Rom import Rom
    from World import World

AddressesDict: TypeAlias = "dict[str, Address | dict[str, Address | dict[str, Address]]]"


class Scenes(IntEnum):
    # Dungeons
    DEKU_TREE = 0x00
    DODONGOS_CAVERN = 0x01
    KING_DODONGO_LOBBY = 0x12
    JABU_JABU = 0x02
    FOREST_TEMPLE = 0x03
    FIRE_TEMPLE = 0x04
    WATER_TEMPLE = 0x05
    SPIRIT_TEMPLE = 0x06
    SHADOW_TEMPLE = 0x07
    # Various overworld scenes
    GRAVEYARD = 0x53
    ZORAS_RIVER = 0x54
    KOKIRI_FOREST = 0x55
    LAKE_HYLIA = 0x57
    GERUDO_VALLEY = 0x5A
    LOST_WOODS = 0x5B
    DESERT_COLOSSUS = 0x5C
    GERUDO_FORTRESS = 0x5D
    DEATH_MOUNTAIN_TRAIL = 0x60
    DEATH_MOUNTAIN_CRATER = 0x61
    GORON_CITY = 0x62


class FlagType(IntEnum):
    CHEST = 0x00
    SWITCH = 0x01
    CLEAR = 0x02
    COLLECT = 0x03
    UNK00 = 0x04 # 0x04 unused
    VISITED_ROOM = 0x05
    VISITED_FLOOR = 0x06


class Address:
    prev_address: int = 0
    EXTENDED_CONTEXT_START = 0x1450

    def __init__(self, address: Optional[int] = None, extended: bool = False, size: int = 4, mask: int = 0xFFFFFFFF, max: Optional[int] = None,
                 choices: Optional[dict[str, int]] = None, value: Optional[str] = None) -> None:
        self.address: int = Address.prev_address if address is None else address
        if extended and address is not None:
            self.address += Address.EXTENDED_CONTEXT_START
        self.value: Optional[str | int] = value
        self.size: int = size
        self.choices: Optional[dict[str, int]] = choices
        self.mask: int = mask

        Address.prev_address = self.address + self.size

        self.bit_offset: int = 0
        while mask & 1 == 0:
            mask = mask >> 1
            self.bit_offset += 1

        self.max: int = mask if max is None else max

    def get_value(self, default: str | int = 0) -> str | int:
        if self.value is None:
            return default
        return self.value

    def get_value_raw(self) -> Optional[int]:
        if self.value is None:
            return None

        value = self.value
        if self.choices is not None:
            value = self.choices[value]
        if not isinstance(value, int):
            raise ValueError("Invalid value type '%s'" % str(value))

        if isinstance(value, bool):
            value = 1 if value else 0
        if value > self.max:
            value = self.max

        value = (value << self.bit_offset) & self.mask
        return value

    def set_value_raw(self, value: int) -> None:
        if value is None:
            self.value = None
            return

        if not isinstance(value, int):
            raise ValueError("Invalid value type '%s'" % str(value))

        value = (value & self.mask) >> self.bit_offset
        if value > self.max:
            value = self.max

        if self.choices is not None:
            for choice_name, choice_value in self.choices.items():
                if choice_value == value:
                    value = choice_name
                    break

        self.value = value

    def get_writes(self, save_context: SaveContext) -> None:
        if self.value is None:
            return

        value = self.get_value_raw()
        if value is None:
            return

        values = zip(Address.to_bytes(value, self.size),
                     Address.to_bytes(self.mask, self.size))

        for i, (byte, mask) in enumerate(values):
            if mask == 0:
                continue
            if mask == 0xFF:
                save_context.write_byte(self.address + i, byte)
            else:
                save_context.write_bits(self.address + i, byte, mask=mask)

    @staticmethod
    def to_bytes(value: int, size: int) -> list[int]:
        ret = []
        for _ in range(size):
            ret.insert(0, value & 0xFF)
            value = value >> 8
        return ret


class SaveContext:
    def __init__(self):
        self.save_bits: dict[int, int] = {}
        self.save_bytes: dict[int, int] = {}
        self.addresses: AddressesDict = self.get_save_context_addresses()

    # will set the bits of value to the offset in the save (or'ing them with what is already there)
    def write_bits(self, address: int, value: int, mask: Optional[int] = None, predicate: Optional[Callable[[int], bool]] = None) -> None:
        if predicate and not predicate(value):
            return

        if mask is not None:
            value = value & mask

        if address in self.save_bytes:
            old_val = self.save_bytes[address]
            if mask is not None:
                old_val &= ~mask
            value = old_val | value
            self.write_byte(address, value, predicate)
        elif address in self.save_bits:
            if mask is not None:
                self.save_bits[address] &= ~mask
            self.save_bits[address] |= value
        else:
            self.save_bits[address] = value

    # will overwrite the byte at offset with the given value
    def write_byte(self, address: int, value: int, predicate: Optional[Callable[[int], bool]] = None) -> None:
        if predicate and not predicate(value):
            return

        if address in self.save_bits:
            del self.save_bits[address]

        self.save_bytes[address] = value

    # will overwrite the byte at offset with the given value
    def write_bytes(self, address: int, bytes: Iterable[int], predicate: Optional[Callable[[int], bool]] = None) -> None:
        for i, value in enumerate(bytes):
            self.write_byte(address + i, value, predicate)

    def write_save_entry(self, address: Address) -> None:
        if isinstance(address, dict):
            for name, sub_address in address.items():
                self.write_save_entry(sub_address)
        elif isinstance(address, list):
            for sub_address in address:
                self.write_save_entry(sub_address)
        else:
            address.get_writes(self)

    def write_permanent_flag(self, scene: int, type: int, byte_offset: int, bit_values: int) -> None:
        # Save format is described here: https://wiki.cloudmodding.com/oot/Save_Format
        # Permanent flags start at offset 0x00D4. Each scene has 7 types of flags, one
        # of which is unused. Each flag type is 4 bytes wide per-scene, thus each scene
        # takes 28 (0x1C) bytes.
        # Scenes and FlagType enums are defined for increased readability when using
        # this function.
        self.write_bits(0x00D4 + scene * 0x1C + type * 0x04 + byte_offset, bit_values)

    # write all flags (int32) of a given type at once
    def write_permanent_flags(self, scene: Scenes, flag_type: FlagType, value: int) -> None:
        byte_value = value.to_bytes(4, byteorder='big', signed=False)
        self.write_bytes(0x00D4 + scene * 0x1C + flag_type * 0x04, byte_value)

    def set_ammo_max(self) -> None:
        ammo_maxes = {
            'stick'     : ('stick_upgrade', [10,  10,  20,  30]),
            'nut'       : ('nut_upgrade',   [20,  20,  30,  40]),
            'bomb'      : ('bomb_bag',      [00,  20,  30,  40]),
            'bow'       : ('quiver',        [00,  30,  40,  50]),
            'slingshot' : ('bullet_bag',    [00,  30,  40,  50]),
            'rupees'    : ('wallet',        [99, 200, 500, 999]),
        }

        for ammo, (upgrade, maxes) in ammo_maxes.items():
            upgrade_count = self.addresses['upgrades'][upgrade].get_value()
            try:
                ammo_max = maxes[upgrade_count]
            except IndexError:
                ammo_max = maxes[-1]
            if ammo == 'rupees':
                self.addresses[ammo].max = ammo_max
            else:
                self.addresses['ammo'][ammo].max = ammo_max

    # will overwrite the byte at offset with the given value
    def write_save_table(self, rom: Rom) -> None:
        self.set_ammo_max()
        for name, address in self.addresses.items():
            self.write_save_entry(address)

        save_table = []
        extended_table = []
        for address, value in self.save_bits.items():
            table = save_table
            if address >= Address.EXTENDED_CONTEXT_START:
                table = extended_table
                address -= Address.EXTENDED_CONTEXT_START
            if value != 0:
                table += [(address & 0xFF00) >> 8, address & 0xFF, 0x00, value]
        for address, value in self.save_bytes.items():
            table = save_table
            if address >= Address.EXTENDED_CONTEXT_START:
                table = extended_table
                address -= Address.EXTENDED_CONTEXT_START
            table += [(address & 0xFF00) >> 8, address & 0xFF, 0x01, value]
        save_table += [0x00,0x00,0x00,0x00]
        extended_table += [0x00,0x00, 0x00,0x00]

        table_len = len(save_table)
        if table_len > 0x400:
            raise Exception("The Initial Save Table has exceeded its maximum capacity: 0x%03X/0x400" % table_len)
        rom.write_bytes(rom.sym('INITIAL_SAVE_DATA'), save_table)
        extended_table_len = len(extended_table)
        if extended_table_len > 0x100:
            raise Exception("The Initial Extended Save Table has exceeded its maximum capacity: 0x%03X/0x100" % extended_table_len)
        rom.write_bytes(rom.sym('EXTENDED_INITIAL_SAVE_DATA'), extended_table)

    def give_bottle(self, item: str, count: int) -> None:
        for bottle_id in range(4):
            item_slot = 'bottle_%d' % (bottle_id + 1)
            if self.addresses['item_slot'][item_slot].get_value(0xFF) != 0xFF:
                continue

            self.addresses['item_slot'][item_slot].value = SaveContext.bottle_types[item]
            count -= 1

            if count == 0:
                return

    def give_health(self, health: float):
        health += self.addresses['health_capacity'].get_value(0x30) / 0x10
        health += self.addresses['quest']['heart_pieces'].get_value() / 4

        self.addresses['health_capacity'].value       = int(health) * 0x10
        self.addresses['health'].value                = int(health) * 0x10
        self.addresses['quest']['heart_pieces'].value = int((health % 1) * 4) * 0x10

    def give_item(self, world: World, item: str, count: int = 1) -> None:
        if item.endswith(')'):
            item_base, implicit_count = item[:-1].rsplit(' (', 1)
            if implicit_count.isdigit():
                item = item_base
                count *= int(implicit_count)

        if item in SaveContext.bottle_types:
            self.give_bottle(item, count)
        elif item in ("Piece of Heart", "Piece of Heart (Treasure Chest Game)"):
            self.give_health(count / 4)
        elif item == "Heart Container":
            self.give_health(count)
        elif item == "Bombchu Item":
            self.give_bombchu_item(world)
        elif item in SaveContext.save_writes_table:
            if item.startswith('Silver Rupee (') or item.startswith('Silver Rupee Pouch ('):
                puzzle = item[:-1].split(' (', 1)[1]
                needed_count = {
                    "Dodongos Cavern Staircase": 5,
                    "Ice Cavern Spinning Scythe": 5,
                    "Ice Cavern Push Block": 5,
                    "Bottom of the Well Basement": 5,
                    "Shadow Temple Scythe Shortcut": 5,
                    "Shadow Temple Invisible Blades": 10,
                    "Shadow Temple Huge Pit": 5,
                    "Shadow Temple Invisible Spikes": 10 if world.dungeon_mq["Shadow Temple"] else 5,
                    "Gerudo Training Ground Slopes": 5,
                    "Gerudo Training Ground Lava": 6 if world.dungeon_mq["Gerudo Training Ground"] else 5,
                    "Gerudo Training Ground Water": 3 if world.dungeon_mq["Gerudo Training Ground"] else 5,
                    "Spirit Temple Child Early Torches": 5,
                    "Spirit Temple Adult Boulders": 5,
                    "Spirit Temple Lobby and Lower Adult": 5,
                    "Spirit Temple Sun Block": 5,
                    "Spirit Temple Adult Climb": 5,
                    "Ganons Castle Spirit Trial": 5,
                    "Ganons Castle Light Trial": 5,
                    "Ganons Castle Fire Trial": 5,
                    "Ganons Castle Shadow Trial": 5,
                    "Ganons Castle Water Trial": 5,
                    "Ganons Castle Forest Trial": 5,
                }[puzzle]
                if item.startswith('Silver Rupee Pouch ('):
                    item = item.replace('Silver Rupee Pouch (', 'Silver Rupee (')
                    count = needed_count
                if count >= needed_count:
                    save_writes = {
                        "Dodongos Cavern Staircase":           {'silver_rupee_counts.dc_staircase': needed_count, 'scene_flags.dodongo.swch.silver_rupees_staircase': True},
                        "Ice Cavern Spinning Scythe":          {'silver_rupee_counts.ice_scythe': needed_count, 'scene_flags.ice.swch.silver_rupees_scythe': True},
                        "Ice Cavern Push Block":               {'silver_rupee_counts.ice_block': needed_count, 'scene_flags.ice.swch.silver_rupees_block': True},
                        "Bottom of the Well Basement":         {'silver_rupee_counts.botw_basement': needed_count, 'scene_flags.botw.swch.silver_rupees_basement': True},
                        "Shadow Temple Scythe Shortcut":       {'silver_rupee_counts.shadow_scythe': needed_count, 'scene_flags.shadow.swch.silver_rupees_scythe': True},
                        "Shadow Temple Invisible Blades":      {'silver_rupee_counts.shadow_blades': needed_count, 'scene_flags.shadow.swch.silver_rupees_blades': True},
                        "Shadow Temple Huge Pit":              {'silver_rupee_counts.shadow_pit': needed_count, 'scene_flags.shadow.swch.silver_rupees_mq_pit' if world.dungeon_mq["Shadow Temple"] else 'scene_flags.shadow.swch.silver_rupees_vanilla_pit': True},
                        "Shadow Temple Invisible Spikes":      {'silver_rupee_counts.shadow_spikes': needed_count, 'scene_flags.shadow.swch.silver_rupees_spikes': True},
                        "Gerudo Training Ground Slopes":       {'silver_rupee_counts.gtg_slopes': needed_count, 'scene_flags.gtg.swch.silver_rupees_slopes': True, 'scene_flags.gtg.clear.slopes_room': True},
                        "Gerudo Training Ground Lava":         {'silver_rupee_counts.gtg_lava': needed_count, 'scene_flags.gtg.swch.silver_rupees_lava': True},
                        "Gerudo Training Ground Water":        {'silver_rupee_counts.gtg_water': needed_count, 'scene_flags.gtg.swch.silver_rupees_water': True},
                        "Spirit Temple Child Early Torches":   {'silver_rupee_counts.spirit_torches': needed_count, 'scene_flags.spirit.swch.silver_rupees_torches': True},
                        "Spirit Temple Adult Boulders":        {'silver_rupee_counts.spirit_boulders': needed_count, 'scene_flags.spirit.swch.silver_rupees_boulders': True},
                        "Spirit Temple Lobby and Lower Adult": {'silver_rupee_counts.spirit_lobby': needed_count, 'scene_flags.spirit.swch.silver_rupees_lobby': True},
                        "Spirit Temple Sun Block":             {'silver_rupee_counts.spirit_sun': needed_count, 'scene_flags.spirit.swch.silver_rupees_sun': True},
                        "Spirit Temple Adult Climb":           {'silver_rupee_counts.spirit_adult_climb': needed_count, 'scene_flags.spirit.swch.silver_rupees_adult_climb': True},
                        "Ganons Castle Spirit Trial":          {'silver_rupee_counts.trials_spirit': needed_count, 'scene_flags.gc.swch.silver_rupees_shadow_spirit': True},
                        "Ganons Castle Light Trial":           {'silver_rupee_counts.trials_light': needed_count, 'scene_flags.gc.swch.silver_rupees_light': True},
                        "Ganons Castle Fire Trial":            {'silver_rupee_counts.trials_fire': needed_count, 'scene_flags.gc.swch.silver_rupees_mq_fire' if world.dungeon_mq["Ganons Castle"] else 'scene_flags.gc.swch.silver_rupees_vanilla_fire': True},
                        "Ganons Castle Shadow Trial":          {'silver_rupee_counts.trials_shadow': needed_count, 'scene_flags.gc.swch.silver_rupees_shadow_spirit': True},
                        "Ganons Castle Water Trial":           {'silver_rupee_counts.trials_water': needed_count, 'scene_flags.gc.swch.silver_rupees_water': True},
                        "Ganons Castle Forest Trial":          {'silver_rupee_counts.trials_forest': needed_count, 'scene_flags.gc.swch.silver_rupees_forest': True},
                    }[puzzle]
                else:
                    save_writes = SaveContext.save_writes_table[item]
            elif item.startswith('Small Key Ring ('):
                dungeon = item[:-1].split(' (', 1)[1]
                save_writes = {
                    "Forest Temple"          : {
                        'keys.forest': 6 if world.dungeon_mq[dungeon] else 5,
                        'total_keys.forest': 6 if world.dungeon_mq[dungeon] else 5,
                    },
                    "Fire Temple"            : {
                        'keys.fire': 5 if world.dungeon_mq[dungeon] else 8,
                        'total_keys.fire': 5 if world.dungeon_mq[dungeon] else 8,
                    },
                    "Water Temple"           : {
                        'keys.water': 2 if world.dungeon_mq[dungeon] else 6,
                        'total_keys.water': 2 if world.dungeon_mq[dungeon] else 6,
                    },
                    "Spirit Temple"          : {
                        'keys.spirit': 7 if world.dungeon_mq[dungeon] else 5,
                        'total_keys.spirit': 7 if world.dungeon_mq[dungeon] else 5,
                    },
                    "Shadow Temple"          : {
                        'keys.shadow': 6 if world.dungeon_mq[dungeon] else 5,
                        'total_keys.shadow': 6 if world.dungeon_mq[dungeon] else 5,
                    },
                    "Bottom of the Well"     : {
                        'keys.botw': 2 if world.dungeon_mq[dungeon] else 3,
                        'total_keys.botw': 2 if world.dungeon_mq[dungeon] else 3,
                    },
                    "Gerudo Training Ground" : {
                        'keys.gtg': 3 if world.dungeon_mq[dungeon] else 9,
                        'total_keys.gtg': 3 if world.dungeon_mq[dungeon] else 9,
                    },
                    "Thieves Hideout"        : {
                        'keys.fortress': 4,
                        'total_keys.fortress': 4,
                    },
                    "Ganons Castle"          : {
                        'keys.gc': 3 if world.dungeon_mq[dungeon] else 2,
                        'total_keys.gc': 3 if world.dungeon_mq[dungeon] else 2,
                    },
                    "Treasure Chest Game"    : {
                        'keys.tcg': 6,
                        'total_keys.tcg': 6,
                    },
                }[dungeon]
                if world.keyring_give_bk(dungeon):
                    bk_names = {
                        "Forest Temple": 'dungeon_items.forest.boss_key',
                        "Fire Temple": 'dungeon_items.fire.boss_key',
                        "Water Temple": 'dungeon_items.water.boss_key',
                        "Spirit Temple": 'dungeon_items.spirit.boss_key',
                        "Shadow Temple": 'dungeon_items.shadow.boss_key',
                    }
                    save_writes[dungeon][bk_names[dungeon]] = True
            else:
                save_writes = SaveContext.save_writes_table[item]
            for address, value in save_writes.items():
                if value is None:
                    value = count
                elif isinstance(value, list):
                    value = value[min(len(value), count) - 1]
                elif isinstance(value, bool):
                    value = 1 if value else 0

                address_value = self.addresses
                prev_sub_address = 'Save Context'
                sub_address = None
                for sub_address in address.split('.'):
                    if sub_address not in address_value:
                        raise ValueError('Unknown key %s in %s of SaveContext' % (sub_address, prev_sub_address))

                    if isinstance(address_value, list):
                        sub_address =  int(sub_address)

                    address_value = address_value[sub_address]
                    prev_sub_address = sub_address
                if not isinstance(address_value, Address):
                    raise ValueError('%s does not resolve to an Address in SaveContext' % sub_address)

                if isinstance(value, int) and value < address_value.get_value():
                    continue

                address_value.value = value
        else:
            raise ValueError("Cannot give unknown starting item %s" % item)

    def give_bombchu_item(self, world: World) -> None:
        self.give_item(world, "Bombchus", 0)

    def equip_default_items(self, age: str) -> None:
        self.equip_items(age, 'equips_' + age)

    def equip_current_items(self, age: str) -> None:
        self.equip_items(age, 'equips')

    def equip_items(self, age: str, equip_type: str) -> None:
        if age not in ['child', 'adult']:
            raise ValueError("Age must be 'adult' or 'child', not %s" % age)

        if equip_type not in ['equips', 'equips_child', 'equips_adult']:
            raise ValueError("Equip type must be 'equips', 'equips_child' or 'equips_adult', not %s" % equip_type)

        age = 'equips_' + age
        c_buttons = list(self.addresses[age]['button_slots'].keys())
        for item_slot in SaveContext.equipable_items[age]['items']:
            item = self.addresses['item_slot'][item_slot].get_value('none')
            if item != 'none':
                c_button = c_buttons.pop()
                self.addresses[equip_type]['button_slots'][c_button].value = item_slot
                self.addresses[equip_type]['button_items'][c_button].value = item
                if not c_buttons:
                    break

        for equip_item, equip_addresses in self.addresses[age]['equips'].items():
            for item in SaveContext.equipable_items[age][equip_item]:
                if self.addresses['equip_items'][item].get_value():
                    item_value = self.addresses['equip_items'][item].get_value_raw()
                    self.addresses[equip_type]['equips'][equip_item].set_value_raw(item_value)
                    if equip_item == 'tunic':
                        self.addresses[equip_type]['equips'][equip_item].value = 1
                    if equip_item == 'sword':
                        self.addresses[equip_type]['button_items']['b'].value = item
                    break

    @staticmethod
    def get_save_context_addresses() -> AddressesDict:
        return {
            'entrance_index'             : Address(0x0000, size=4),
            'link_age'                   : Address(size=4, max=1),
            'unk_00'                     : Address(size=2),
            'cutscene_index'             : Address(size=2),
            'time_of_day'                : Address(size=2),
            'unk_01'                     : Address(size=2),
            'night_flag'                 : Address(size=4, max=1),
            'unk_02'                     : Address(size=8),
            'id'                         : Address(size=6),
            'deaths'                     : Address(size=2),
            'file_name'                  : Address(size=8),
            'n64dd_flag'                 : Address(size=2),
            'health_capacity'            : Address(size=2, max=0x140),
            'health'                     : Address(size=2, max=0x140),
            'magic_level'                : Address(size=1, max=2),
            'magic'                      : Address(size=1, max=0x60),
            'rupees'                     : Address(size=2),
            'bgs_hits_left'              : Address(size=2),
            'navi_timer'                 : Address(size=2),
            'magic_acquired'             : Address(size=1, max=1),
            'unk_03'                     : Address(size=1),
            'double_magic'               : Address(size=1, max=1),
            'double_defense'             : Address(size=1, max=1),
            'bgs_flag'                   : Address(size=1, max=1),
            'unk_05'                     : Address(size=1),

            # Equiped Items
            'equips_child' : {
                'button_items' : {
                    'b'                  : Address(size=1, choices=SaveContext.item_id_map),
                    'left'               : Address(size=1, choices=SaveContext.item_id_map),
                    'down'               : Address(size=1, choices=SaveContext.item_id_map),
                    'right'              : Address(size=1, choices=SaveContext.item_id_map),
                },
                'button_slots' : {
                    'left'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'down'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'right'              : Address(size=1, choices=SaveContext.slot_id_map),
                },
                'equips' : {
                    'sword'              : Address(0x0048, size=2, mask=0x000F),
                    'shield'             : Address(0x0048, size=2, mask=0x00F0),
                    'tunic'              : Address(0x0048, size=2, mask=0x0F00),
                    'boots'              : Address(0x0048, size=2, mask=0xF000),
                },
            },
            'equips_adult' : {
                'button_items' : {
                    'b'                  : Address(size=1, choices=SaveContext.item_id_map),
                    'left'               : Address(size=1, choices=SaveContext.item_id_map),
                    'down'               : Address(size=1, choices=SaveContext.item_id_map),
                    'right'              : Address(size=1, choices=SaveContext.item_id_map),
                },
                'button_slots' : {
                    'left'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'down'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'right'              : Address(size=1, choices=SaveContext.slot_id_map),
                },
                'equips' : {
                    'sword'              : Address(0x0052, size=2, mask=0x000F),
                    'shield'             : Address(0x0052, size=2, mask=0x00F0),
                    'tunic'              : Address(0x0052, size=2, mask=0x0F00),
                    'boots'              : Address(0x0052, size=2, mask=0xF000),
                },
            },
            'unk_06'                     : Address(size=0x12),
            'scene_index'                : Address(size=2),

            'equips' : {
                'button_items' : {
                    'b'                  : Address(size=1, choices=SaveContext.item_id_map),
                    'left'               : Address(size=1, choices=SaveContext.item_id_map),
                    'down'               : Address(size=1, choices=SaveContext.item_id_map),
                    'right'              : Address(size=1, choices=SaveContext.item_id_map),
                },
                'button_slots' : {
                    'left'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'down'               : Address(size=1, choices=SaveContext.slot_id_map),
                    'right'              : Address(size=1, choices=SaveContext.slot_id_map),
                },
                'equips' : {
                    'sword'              : Address(0x0070, size=2, mask=0x000F, max=3),
                    'shield'             : Address(0x0070, size=2, mask=0x00F0, max=3),
                    'tunic'              : Address(0x0070, size=2, mask=0x0F00, max=3),
                    'boots'              : Address(0x0070, size=2, mask=0xF000, max=3),
                },
            },
            'unk_07'                     : Address(size=2),

            # Item Slots
            'item_slot'                  : {
                'stick'                  : Address(size=1, choices=SaveContext.item_id_map),
                'nut'                    : Address(size=1, choices=SaveContext.item_id_map),
                'bomb'                   : Address(size=1, choices=SaveContext.item_id_map),
                'bow'                    : Address(size=1, choices=SaveContext.item_id_map),
                'fire_arrow'             : Address(size=1, choices=SaveContext.item_id_map),
                'dins_fire'              : Address(size=1, choices=SaveContext.item_id_map),
                'slingshot'              : Address(size=1, choices=SaveContext.item_id_map),
                'ocarina'                : Address(size=1, choices=SaveContext.item_id_map),
                'bombchu'                : Address(size=1, choices=SaveContext.item_id_map),
                'hookshot'               : Address(size=1, choices=SaveContext.item_id_map),
                'ice_arrow'              : Address(size=1, choices=SaveContext.item_id_map),
                'farores_wind'           : Address(size=1, choices=SaveContext.item_id_map),
                'boomerang'              : Address(size=1, choices=SaveContext.item_id_map),
                'lens'                   : Address(size=1, choices=SaveContext.item_id_map),
                'beans'                  : Address(size=1, choices=SaveContext.item_id_map),
                'hammer'                 : Address(size=1, choices=SaveContext.item_id_map),
                'light_arrow'            : Address(size=1, choices=SaveContext.item_id_map),
                'nayrus_love'            : Address(size=1, choices=SaveContext.item_id_map),
                'bottle_1'               : Address(size=1, choices=SaveContext.item_id_map),
                'bottle_2'               : Address(size=1, choices=SaveContext.item_id_map),
                'bottle_3'               : Address(size=1, choices=SaveContext.item_id_map),
                'bottle_4'               : Address(size=1, choices=SaveContext.item_id_map),
                'adult_trade'            : Address(size=1, choices=SaveContext.item_id_map),
                'child_trade'            : Address(size=1, choices=SaveContext.item_id_map),
            },

            # Item Ammo
            'ammo' : {
                'stick'                  : Address(size=1),
                'nut'                    : Address(size=1),
                'bomb'                   : Address(size=1),
                'bow'                    : Address(size=1),
                'fire_arrow'             : Address(size=1, max=0),
                'dins_fire'              : Address(size=1, max=0),
                'slingshot'              : Address(size=1),
                'ocarina'                : Address(size=1, max=0),
                'bombchu'                : Address(size=1, max=50),
                'hookshot'               : Address(size=1, max=0),
                'ice_arrow'              : Address(size=1, max=0),
                'farores_wind'           : Address(size=1, max=0),
                'boomerang'              : Address(size=1, max=0),
                'lens'                   : Address(size=1, max=0),
                'beans'                  : Address(size=1, max=10),
            },
            'magic_beans_sold'           : Address(size=1, max=10),

            # Equipment
            'equip_items' : {
                'kokiri_sword'           : Address(0x009C, size=2, mask=0x0001),
                'master_sword'           : Address(0x009C, size=2, mask=0x0002),
                'biggoron_sword'         : Address(0x009C, size=2, mask=0x0004),
                'broken_giants_knife'    : Address(0x009C, size=2, mask=0x0008),
                'deku_shield'            : Address(0x009C, size=2, mask=0x0010),
                'hylian_shield'          : Address(0x009C, size=2, mask=0x0020),
                'mirror_shield'          : Address(0x009C, size=2, mask=0x0040),
                'kokiri_tunic'           : Address(0x009C, size=2, mask=0x0100),
                'goron_tunic'            : Address(0x009C, size=2, mask=0x0200),
                'zora_tunic'             : Address(0x009C, size=2, mask=0x0400),
                'kokiri_boots'           : Address(0x009C, size=2, mask=0x1000),
                'iron_boots'             : Address(0x009C, size=2, mask=0x2000),
                'hover_boots'            : Address(0x009C, size=2, mask=0x4000),
            },

            'unk_08'                     : Address(size=2),

            # Upgrades
            'upgrades' : {
                'quiver'                 : Address(0x00A0, mask=0x00000007, max=3),
                'bomb_bag'               : Address(0x00A0, mask=0x00000038, max=3),
                'strength_upgrade'       : Address(0x00A0, mask=0x000001C0, max=3),
                'diving_upgrade'         : Address(0x00A0, mask=0x00000E00, max=2),
                'wallet'                 : Address(0x00A0, mask=0x00003000, max=3),
                'bullet_bag'             : Address(0x00A0, mask=0x0001C000, max=3),
                'stick_upgrade'          : Address(0x00A0, mask=0x000E0000, max=3),
                'nut_upgrade'            : Address(0x00A0, mask=0x00700000, max=3),
            },

            # Medallions
            'quest' : {
                'medallions' : {
                    'forest'             : Address(0x00A4, mask=0x00000001),
                    'fire'               : Address(0x00A4, mask=0x00000002),
                    'water'              : Address(0x00A4, mask=0x00000004),
                    'spirit'             : Address(0x00A4, mask=0x00000008),
                    'shadow'             : Address(0x00A4, mask=0x00000010),
                    'light'              : Address(0x00A4, mask=0x00000020),

                },
                'songs' : {
                    'minuet_of_forest'   : Address(0x00A4, mask=0x00000040),
                    'bolero_of_fire'     : Address(0x00A4, mask=0x00000080),
                    'serenade_of_water'  : Address(0x00A4, mask=0x00000100),
                    'requiem_of_spirit'  : Address(0x00A4, mask=0x00000200),
                    'nocturne_of_shadow' : Address(0x00A4, mask=0x00000400),
                    'prelude_of_light'   : Address(0x00A4, mask=0x00000800),
                    'zeldas_lullaby'     : Address(0x00A4, mask=0x00001000),
                    'eponas_song'        : Address(0x00A4, mask=0x00002000),
                    'sarias_song'        : Address(0x00A4, mask=0x00004000),
                    'suns_song'          : Address(0x00A4, mask=0x00008000),
                    'song_of_time'       : Address(0x00A4, mask=0x00010000),
                    'song_of_storms'     : Address(0x00A4, mask=0x00020000),
                },
                'stones' : {
                    'kokiris_emerald'    : Address(0x00A4, mask=0x00040000),
                    'gorons_ruby'        : Address(0x00A4, mask=0x00080000),
                    'zoras_sapphire'     : Address(0x00A4, mask=0x00100000),
                },
                'stone_of_agony'         : Address(0x00A4, mask=0x00200000),
                'gerudos_card'           : Address(0x00A4, mask=0x00400000),
                'gold_skulltula'         : Address(0x00A4, mask=0x00800000),
                'heart_pieces'           : Address(0x00A4, mask=0xFF000000),
            },

            # Dungeon Items
            'dungeon_items' : {
                'deku' : {
                     'boss_key'          : Address(0x00A8, size=1, mask=0x01),
                     'compass'           : Address(0x00A8, size=1, mask=0x02),
                     'map'               : Address(0x00A8, size=1, mask=0x04),
                },
                'dodongo' : {
                     'boss_key'          : Address(0x00A9, size=1, mask=0x01),
                     'compass'           : Address(0x00A9, size=1, mask=0x02),
                     'map'               : Address(0x00A9, size=1, mask=0x04),
                },
                'jabu' : {
                     'boss_key'          : Address(0x00AA, size=1, mask=0x01),
                     'compass'           : Address(0x00AA, size=1, mask=0x02),
                     'map'               : Address(0x00AA, size=1, mask=0x04),
                },
                'forest' : {
                     'boss_key'          : Address(0x00AB, size=1, mask=0x01),
                     'compass'           : Address(0x00AB, size=1, mask=0x02),
                     'map'               : Address(0x00AB, size=1, mask=0x04),
                },
                'fire' : {
                     'boss_key'          : Address(0x00AC, size=1, mask=0x01),
                     'compass'           : Address(0x00AC, size=1, mask=0x02),
                     'map'               : Address(0x00AC, size=1, mask=0x04),
                },
                'water' : {
                     'boss_key'          : Address(0x00AD, size=1, mask=0x01),
                     'compass'           : Address(0x00AD, size=1, mask=0x02),
                     'map'               : Address(0x00AD, size=1, mask=0x04),
                },
                'spirit' : {
                     'boss_key'          : Address(0x00AE, size=1, mask=0x01),
                     'compass'           : Address(0x00AE, size=1, mask=0x02),
                     'map'               : Address(0x00AE, size=1, mask=0x04),
                },
                'shadow' : {
                     'boss_key'          : Address(0x00AF, size=1, mask=0x01),
                     'compass'           : Address(0x00AF, size=1, mask=0x02),
                     'map'               : Address(0x00AF, size=1, mask=0x04),
                },
                'botw' : {
                     'boss_key'          : Address(0x00B0, size=1, mask=0x01),
                     'compass'           : Address(0x00B0, size=1, mask=0x02),
                     'map'               : Address(0x00B0, size=1, mask=0x04),
                },
                'ice' : {
                     'boss_key'          : Address(0x00B1, size=1, mask=0x01),
                     'compass'           : Address(0x00B1, size=1, mask=0x02),
                     'map'               : Address(0x00B1, size=1, mask=0x04),
                },
                'gt' : {
                     'boss_key'          : Address(0x00B2, size=1, mask=0x01),
                     'compass'           : Address(0x00B2, size=1, mask=0x02),
                     'map'               : Address(0x00B2, size=1, mask=0x04),
                },
                'gtg' : {
                     'boss_key'          : Address(0x00B3, size=1, mask=0x01),
                     'compass'           : Address(0x00B3, size=1, mask=0x02),
                     'map'               : Address(0x00B3, size=1, mask=0x04),
                },
                'fortress' : {
                     'boss_key'          : Address(0x00B4, size=1, mask=0x01),
                     'compass'           : Address(0x00B4, size=1, mask=0x02),
                     'map'               : Address(0x00B4, size=1, mask=0x04),
                },
                'gc' : {
                     'boss_key'          : Address(0x00B5, size=1, mask=0x01),
                     'compass'           : Address(0x00B5, size=1, mask=0x02),
                     'map'               : Address(0x00B5, size=1, mask=0x04),
                },
                'unused'                 : Address(size=6),
            },
            'keys' : {
                'deku'                   : Address(size=1),
                'dodongo'                : Address(size=1),
                'jabu'                   : Address(size=1),
                'forest'                 : Address(size=1),
                'fire'                   : Address(size=1),
                'water'                  : Address(size=1),
                'spirit'                 : Address(size=1),
                'shadow'                 : Address(size=1),
                'botw'                   : Address(size=1),
                'ice'                    : Address(size=1),
                'gt'                     : Address(size=1),
                'gtg'                    : Address(size=1),
                'fortress'               : Address(size=1),
                'gc'                     : Address(size=1),
                'gt_col'                 : Address(size=1),
                'gc_col'                 : Address(size=1),
                'tcg'                    : Address(size=1),
                'unused'                 : Address(size=2),
            },
            'defense_hearts'             : Address(size=1, max=20),
            'gs_tokens'                  : Address(size=2, max=100),
            'total_keys' : { # Upper half of unused word in own scene
                'deku'                   : Address(0xD4 + 0x1C * 0x00 + 0x10, size=2),
                'dodongo'                : Address(0xD4 + 0x1C * 0x01 + 0x10, size=2),
                'jabu'                   : Address(0xD4 + 0x1C * 0x02 + 0x10, size=2),
                'forest'                 : Address(0xD4 + 0x1C * 0x03 + 0x10, size=2),
                'fire'                   : Address(0xD4 + 0x1C * 0x04 + 0x10, size=2),
                'water'                  : Address(0xD4 + 0x1C * 0x05 + 0x10, size=2),
                'spirit'                 : Address(0xD4 + 0x1C * 0x06 + 0x10, size=2),
                'shadow'                 : Address(0xD4 + 0x1C * 0x07 + 0x10, size=2),
                'botw'                   : Address(0xD4 + 0x1C * 0x08 + 0x10, size=2),
                'ice'                    : Address(0xD4 + 0x1C * 0x09 + 0x10, size=2),
                'gt'                     : Address(0xD4 + 0x1C * 0x0A + 0x10, size=2),
                'gtg'                    : Address(0xD4 + 0x1C * 0x0B + 0x10, size=2),
                'fortress'               : Address(0xD4 + 0x1C * 0x0C + 0x10, size=2),
                'gc'                     : Address(0xD4 + 0x1C * 0x0D + 0x10, size=2),
                'tcg'                    : Address(0xD4 + 0x1C * 0x10 + 0x10, size=2),
            },
            'scene_flags' : {
                'dodongo' : {
                    'swch' : {
                        'silver_rupees_staircase': Address(0xD4 + 0x1C * 0x01 + 0x04, mask=0x80000000),
                    },
                },
                'spirit' : {
                    'swch' : {
                        'silver_rupees_adult_climb': Address(0xD4 + 0x1C * 0x06 + 0x04, mask=0x00000001),
                        'silver_rupees_boulders': Address(0xD4 + 0x1C * 0x06 + 0x04, mask=0x00000004),
                        'silver_rupees_torches': Address(0xD4 + 0x1C * 0x06 + 0x04, mask=0x00000020),
                        'silver_rupees_sun': Address(0xD4 + 0x1C * 0x06 + 0x04, mask=0x00000400),
                        'silver_rupees_lobby': Address(0xD4 + 0x1C * 0x06 + 0x04, mask=0x80000000),
                    },
                },
                'shadow' : {
                    'swch' : {
                        'silver_rupees_scythe': Address(0xD4 + 0x1C * 0x07 + 0x04, mask=0x00000002),
                        'silver_rupees_blades': Address(0xD4 + 0x1C * 0x07 + 0x04, mask=0x00000008),
                        'silver_rupees_spikes': Address(0xD4 + 0x1C * 0x07 + 0x04, mask=0x00000100),
                        'silver_rupees_vanilla_pit': Address(0xD4 + 0x1C * 0x07 + 0x04, mask=0x00000200),
                        'silver_rupees_mq_pit': Address(0xD4 + 0x1C * 0x07 + 0x04, mask=0x00020000),
                    },
                },
                'botw' : {
                    'swch' : {
                        'silver_rupees_basement': Address(0xD4 + 0x1C * 0x08 + 0x04, mask=0x80000000),
                    },
                },
                'ice' : {
                    'swch' : {
                        'silver_rupees_scythe': Address(0xD4 + 0x1C * 0x09 + 0x04, mask=0x00000100),
                        'silver_rupees_block': Address(0xD4 + 0x1C * 0x09 + 0x04, mask=0x00000200),
                    },
                },
                'gtg' : {
                    'swch' : {
                        'silver_rupees_lava': Address(0xD4 + 0x1C * 0x0B + 0x04, mask=0x00001000),
                        'silver_rupees_water': Address(0xD4 + 0x1C * 0x0B + 0x04, mask=0x08000000),
                        'silver_rupees_slopes': Address(0xD4 + 0x1C * 0x0B + 0x04, mask=0x10000000),
                    },
                    'clear' : {
                        'slopes_room' : Address(0xD4 + 0x1C*0x0B + 0x08, mask=0x00000004)
                    }
                },
                'gc' : {
                    'swch' : {
                        'silver_rupees_mq_fire': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00000002),
                        'silver_rupees_water': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00000004),
                        'silver_rupees_vanilla_fire': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00000200),
                        'silver_rupees_shadow_spirit': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00000800),
                        'silver_rupees_forest': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00004000),
                        'silver_rupees_light': Address(0xD4 + 0x1C * 0x0D + 0x04, mask=0x00040000),
                    },
                },
            },
            'triforce_pieces'            : Address(0xD4 + 0x1C * 0x48 + 0x10, size=4), # Unused word in scene x48
            'pending_freezes'            : Address(0xD4 + 0x1C * 0x49 + 0x10, size=4), # Unused word in scene x49
            'ocarina_buttons' : { # Unused word in scene x50
                'a'                      : Address(0xD4 + 0x1C * 0x50 + 0x10, size=4, mask=0x00000001),
                'c_up'                   : Address(0xD4 + 0x1C * 0x50 + 0x10, size=4, mask=0x00000002),
                'c_down'                 : Address(0xD4 + 0x1C * 0x50 + 0x10, size=4, mask=0x00000004),
                'c_left'                 : Address(0xD4 + 0x1C * 0x50 + 0x10, size=4, mask=0x00000008),
                'c_right'                : Address(0xD4 + 0x1C * 0x50 + 0x10, size=4, mask=0x00000010),
            },
            'owned_trade_items' : { # Unused word in scene x60
                'weird_egg'              : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000001),
                'chicken'                : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000002),
                'zeldas_letter'          : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000004),
                'keaton_mask'            : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000008),
                'skull_mask'             : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000010),
                'spooky_mask'            : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000020),
                'bunny_hood'             : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000040),
                'goron_mask'             : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000080),
                'zora_mask'              : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000100),
                'gerudo_mask'            : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000200),
                'mask_of_truth'          : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000400),
                'pocket_egg'             : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00000800),
                'pocket_cucco'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00001000),
                'cojiro'                 : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00002000),
                'odd_mushroom'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00004000),
                'odd_potion'             : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00008000),
                'poachers_saw'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00010000),
                'broken_sword'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00020000),
                'prescription'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00040000),
                'eyeball_frog'           : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00080000),
                'eye_drops'              : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00100000),
                'claim_check'            : Address(0xD4 + 0x1C * 0x60 + 0x10, size=4, mask=0x00200000),
            },

            # begin extended save data items
            'silver_rupee_counts' : {
                'dc_staircase': Address(address=0x00, extended=True, size=1),
                'ice_scythe': Address(extended=True, size=1),
                'ice_block': Address(extended=True, size=1),
                'botw_basement': Address(extended=True, size=1),
                'shadow_scythe': Address(extended=True, size=1),
                'shadow_blades': Address(extended=True, size=1),
                'shadow_pit': Address(extended=True, size=1),
                'shadow_spikes': Address(extended=True, size=1),
                'gtg_slopes': Address(extended=True, size=1),
                'gtg_lava': Address(extended=True, size=1),
                'gtg_water': Address(extended=True, size=1),
                'spirit_torches': Address(extended=True, size=1),
                'spirit_boulders': Address(extended=True, size=1),
                'spirit_lobby': Address(extended=True, size=1),
                'spirit_sun': Address(extended=True, size=1),
                'spirit_adult_climb': Address(extended=True, size=1),
                'trials_spirit': Address(extended=True, size=1),
                'trials_light': Address(extended=True, size=1),
                'trials_fire': Address(extended=True, size=1),
                'trials_shadow': Address(extended=True, size=1),
                'trials_water': Address(extended=True, size=1),
                'trials_forest': Address(extended=True, size=1),
            },
            'password' : Address(extended=True, size=6),

        }

    item_id_map: dict[str, int] = {
        'none'                : 0xFF,
        'stick'               : 0x00,
        'nut'                 : 0x01,
        'bomb'                : 0x02,
        'bow'                 : 0x03,
        'fire_arrow'          : 0x04,
        'dins_fire'           : 0x05,
        'slingshot'           : 0x06,
        'fairy_ocarina'       : 0x07,
        'ocarina_of_time'     : 0x08,
        'bombchu'             : 0x09,
        'hookshot'            : 0x0A,
        'longshot'            : 0x0B,
        'ice_arrow'           : 0x0C,
        'farores_wind'        : 0x0D,
        'boomerang'           : 0x0E,
        'lens'                : 0x0F,
        'beans'               : 0x10,
        'hammer'              : 0x11,
        'light_arrow'         : 0x12,
        'nayrus_love'         : 0x13,
        'bottle'              : 0x14,
        'red_potion'          : 0x15,
        'green_potion'        : 0x16,
        'blue_potion'         : 0x17,
        'fairy'               : 0x18,
        'fish'                : 0x19,
        'milk'                : 0x1A,
        'letter'              : 0x1B,
        'blue_fire'           : 0x1C,
        'bug'                 : 0x1D,
        'big_poe'             : 0x1E,
        'half_milk'           : 0x1F,
        'poe'                 : 0x20,
        'weird_egg'           : 0x21,
        'chicken'             : 0x22,
        'zeldas_letter'       : 0x23,
        'keaton_mask'         : 0x24,
        'skull_mask'          : 0x25,
        'spooky_mask'         : 0x26,
        'bunny_hood'          : 0x27,
        'goron_mask'          : 0x28,
        'zora_mask'           : 0x29,
        'gerudo_mask'         : 0x2A,
        'mask_of_truth'       : 0x2B,
        'sold_out'            : 0x2C,
        'pocket_egg'          : 0x2D,
        'pocket_cucco'        : 0x2E,
        'cojiro'              : 0x2F,
        'odd_mushroom'        : 0x30,
        'odd_potion'          : 0x31,
        'poachers_saw'        : 0x32,
        'broken_sword'        : 0x33,
        'prescription'        : 0x34,
        'eyeball_frog'        : 0x35,
        'eye_drops'           : 0x36,
        'claim_check'         : 0x37,
        'bow_fire_arrow'      : 0x38,
        'bow_ice_arrow'       : 0x39,
        'bow_light_arrow'     : 0x3A,
        'kokiri_sword'        : 0x3B,
        'master_sword'        : 0x3C,
        'biggoron_sword'      : 0x3D,
        'deku_shield'         : 0x3E,
        'hylian_shield'       : 0x3F,
        'mirror_shield'       : 0x40,
        'kokiri_tunic'        : 0x41,
        'goron_tunic'         : 0x42,
        'zora_tunic'          : 0x43,
        'kokiri_boots'        : 0x44,
        'iron_boots'          : 0x45,
        'hover_boots'         : 0x46,
        'bullet_bag_30'       : 0x47,
        'bullet_bag_40'       : 0x48,
        'bullet_bag_50'       : 0x49,
        'quiver_30'           : 0x4A,
        'quiver_40'           : 0x4B,
        'quiver_50'           : 0x4C,
        'bomb_bag_20'         : 0x4D,
        'bomb_bag_30'         : 0x4E,
        'bomb_bag_40'         : 0x4F,
        'gorons_bracelet'     : 0x40,
        'silver_gauntlets'    : 0x41,
        'golden_gauntlets'    : 0x42,
        'silver_scale'        : 0x43,
        'golden_scale'        : 0x44,
        'broken_giants_knife' : 0x45,
        'adults_wallet'       : 0x46,
        'giants_wallet'       : 0x47,
        'deku_seeds'          : 0x48,
        'fishing_pole'        : 0x49,
        'minuet'              : 0x4A,
        'bolero'              : 0x4B,
        'serenade'            : 0x4C,
        'requiem'             : 0x4D,
        'nocturne'            : 0x4E,
        'prelude'             : 0x4F,
        'zeldas_lullaby'      : 0x50,
        'eponas_song'         : 0x51,
        'sarias_song'         : 0x52,
        'suns_song'           : 0x53,
        'song_of_time'        : 0x54,
        'song_of_storms'      : 0x55,
        'forest_medallion'    : 0x56,
        'fire_medallion'      : 0x57,
        'water_medallion'     : 0x58,
        'spirit_medallion'    : 0x59,
        'shadow_medallion'    : 0x5A,
        'light_medallion'     : 0x5B,
        'kokiris_emerald'     : 0x5C,
        'gorons_ruby'         : 0x5D,
        'zoras_sapphire'      : 0x5E,
        'stone_of_agony'      : 0x5F,
        'gerudos_card'        : 0x60,
        'gold_skulltula'      : 0x61,
        'heart_container'     : 0x62,
        'piece_of_heart'      : 0x63,
        'boss_key'            : 0x64,
        'compass'             : 0x65,
        'dungeon_map'         : 0x66,
        'small_key'           : 0x67,
    }

    slot_id_map: dict[str, int] = {
        'stick'               : 0x00,
        'nut'                 : 0x01,
        'bomb'                : 0x02,
        'bow'                 : 0x03,
        'fire_arrow'          : 0x04,
        'dins_fire'           : 0x05,
        'slingshot'           : 0x06,
        'ocarina'             : 0x07,
        'bombchu'             : 0x08,
        'hookshot'            : 0x09,
        'ice_arrow'           : 0x0A,
        'farores_wind'        : 0x0B,
        'boomerang'           : 0x0C,
        'lens'                : 0x0D,
        'beans'               : 0x0E,
        'hammer'              : 0x0F,
        'light_arrow'         : 0x10,
        'nayrus_love'         : 0x11,
        'bottle_1'            : 0x12,
        'bottle_2'            : 0x13,
        'bottle_3'            : 0x14,
        'bottle_4'            : 0x15,
        'adult_trade'         : 0x16,
        'child_trade'         : 0x17,
    }

    bottle_types: dict[str, str] = {
        "Bottle"                   : 'bottle',
        "Bottle with Red Potion"   : 'red_potion',
        "Bottle with Green Potion" : 'green_potion',
        "Bottle with Blue Potion"  : 'blue_potion',
        "Bottle with Fairy"        : 'fairy',
        "Bottle with Fish"         : 'fish',
        "Bottle with Milk"         : 'milk',
        "Rutos Letter"             : 'letter',
        "Bottle with Blue Fire"    : 'blue_fire',
        "Bottle with Bugs"         : 'bug',
        "Bottle with Big Poe"      : 'big_poe',
        "Bottle with Milk (Half)"  : 'half_milk',
        "Bottle with Poe"          : 'poe',
    }

    save_writes_table: dict[str, dict[str, Any]] = {
        "Nothing"        : {},
        "Recovery Heart" : {},
        "Fairy Drop"     : {},
        "Deku Stick Capacity": {
            'item_slot.stick'            : 'stick',
            'upgrades.stick_upgrade'     : [2, 3],
        },
        "Deku Stick": {
            'item_slot.stick'            : 'stick',
            'upgrades.stick_upgrade'     : 1,
            'ammo.stick'                 : None,
        },
        "Deku Sticks": {
            'item_slot.stick'            : 'stick',
            'upgrades.stick_upgrade'     : 1,
            'ammo.stick'                 : None,
        },
        "Deku Nut Capacity": {
            'item_slot.nut'              : 'nut',
            'upgrades.nut_upgrade'       : [2, 3],
        },
        "Deku Nuts": {
            'item_slot.nut'              : 'nut',
            'upgrades.nut_upgrade'       : 1,
            'ammo.nut'                   : None,
        },
        "Bomb Bag": {
            'item_slot.bomb'             : 'bomb',
            'upgrades.bomb_bag'          : None,
        },
        "Bombs" : {
            'ammo.bomb'                  : None,
        },
        "Bombchus" : {
            'item_slot.bombchu'          : 'bombchu',
            'ammo.bombchu'               : None,
        },
        "Bow" : {
            'item_slot.bow'              : 'bow',
            'upgrades.quiver'            : None,
        },
        "Arrows" : {
            'ammo.bow'                   : None,
        },
        "Slingshot"    : {
            'item_slot.slingshot'        : 'slingshot',
            'upgrades.bullet_bag'        : None,
        },
        "Deku Seeds" : {
            'ammo.slingshot'             : None,
        },
        "Magic Bean" : {
            'item_slot.beans'            : 'beans',
            'ammo.beans'                 : None,
            'magic_beans_sold'           : None,
        },
        "Fire Arrows"    : {'item_slot.fire_arrow'      : 'fire_arrow'},
        "Ice Arrows"     : {'item_slot.ice_arrow'       : 'ice_arrow'},
        "Blue Fire Arrows"     : {'item_slot.ice_arrow' : 'ice_arrow'},
        "Light Arrows"   : {'item_slot.light_arrow'     : 'light_arrow'},
        "Dins Fire"      : {'item_slot.dins_fire'       : 'dins_fire'},
        "Farores Wind"   : {'item_slot.farores_wind'    : 'farores_wind'},
        "Nayrus Love"    : {'item_slot.nayrus_love'     : 'nayrus_love'},
        "Ocarina"        : {'item_slot.ocarina'         : ['fairy_ocarina', 'ocarina_of_time']},
        "Progressive Hookshot" : {'item_slot.hookshot'  : ['hookshot', 'longshot']},
        "Boomerang"      : {'item_slot.boomerang'       : 'boomerang'},
        "Lens of Truth"  : {'item_slot.lens'            : 'lens'},
        "Megaton Hammer"         : {'item_slot.hammer'          : 'hammer'},
        "Pocket Egg"     : {
            'item_slot.adult_trade'               : 'pocket_egg',
            'owned_trade_items.pocket_egg'        : True,
        },
        "Pocket Cucco"   : {
            'item_slot.adult_trade'               : 'pocket_cucco',
            'owned_trade_items.pocket_cucco'      : True,
        },
        "Cojiro"         : {
            'item_slot.adult_trade'               : 'cojiro',
            'owned_trade_items.cojiro'            : True,
        },
        "Odd Mushroom"   : {
            'item_slot.adult_trade'               : 'odd_mushroom',
            'owned_trade_items.odd_mushroom'      : True,
        },
        "Odd Potion"     : {
            'item_slot.adult_trade'               : 'odd_potion',
            'owned_trade_items.odd_potion'        : True,
        },
        "Poachers Saw"   : {
            'item_slot.adult_trade'               : 'poachers_saw',
            'owned_trade_items.poachers_saw'      : True,
        },
        "Broken Sword"   : {
            'item_slot.adult_trade'               : 'broken_sword',
            'owned_trade_items.broken_sword'      : True,
        },
        "Prescription"   : {
            'item_slot.adult_trade'               : 'prescription',
            'owned_trade_items.prescription'      : True,
        },
        "Eyeball Frog"   : {
            'item_slot.adult_trade'               : 'eyeball_frog',
            'owned_trade_items.eyeball_frog'      : True,
        },
        "Eyedrops"       : {
            'item_slot.adult_trade'               : 'eye_drops',
            'owned_trade_items.eye_drops'         : True,
        },
        "Claim Check"    : {
            'item_slot.adult_trade'               : 'claim_check',
            'owned_trade_items.claim_check'       : True,
        },
        "Weird Egg"      : {
            'item_slot.child_trade'               : 'weird_egg',
            'owned_trade_items.weird_egg'         : True,
        },
        "Chicken"        : {
            'item_slot.child_trade'               : 'chicken',
            'owned_trade_items.chicken'           : True,
        },
        "Zeldas Letter"  : {
            'item_slot.child_trade'               : 'zeldas_letter',
            'owned_trade_items.zeldas_letter'     : True,
        },
        "Keaton Mask"    : {
            'item_slot.child_trade'               : 'keaton_mask',
            'owned_trade_items.keaton_mask'       : True,
        },
        "Skull Mask"     : {
            'item_slot.child_trade'               : 'skull_mask',
            'owned_trade_items.skull_mask'        : True,
        },
        "Spooky Mask"    : {
            'item_slot.child_trade'               : 'spooky_mask',
            'owned_trade_items.spooky_mask'       : True,
        },
        "Bunny Hood"     : {
            'item_slot.child_trade'               : 'bunny_hood',
            'owned_trade_items.bunny_hood'        : True,
        },
        "Goron Mask"     : {
            'item_slot.child_trade'               : 'goron_mask',
            'owned_trade_items.goron_mask'        : True,
        },
        "Zora Mask"      : {
            'item_slot.child_trade'               : 'zora_mask',
            'owned_trade_items.zora_mask'         : True,
        },
        "Gerudo Mask"    : {
            'item_slot.child_trade'               : 'gerudo_mask',
            'owned_trade_items.gerudo_mask'       : True,
        },
        "Mask of Truth"  : {
            'item_slot.child_trade'               : 'mask_of_truth',
            'owned_trade_items.mask_of_truth'     : True,
        },
        "Goron Tunic"    : {'equip_items.goron_tunic'   : True},
        "Zora Tunic"     : {'equip_items.zora_tunic'    : True},
        "Iron Boots"     : {'equip_items.iron_boots'    : True},
        "Hover Boots"    : {'equip_items.hover_boots'   : True},
        "Deku Shield"    : {'equip_items.deku_shield'   : True},
        "Hylian Shield"  : {'equip_items.hylian_shield' : True},
        "Mirror Shield"  : {'equip_items.mirror_shield' : True},
        "Kokiri Sword"   : {'equip_items.kokiri_sword'  : True},
        "Master Sword"   : {'equip_items.master_sword'  : True},
        "Giants Knife" : {
            'equip_items.biggoron_sword' : True,
            'bgs_hits_left'              : 8,
        },
        "Biggoron Sword" : {
            'equip_items.biggoron_sword' : True,
            'bgs_flag'                   : True,
            'bgs_hits_left'              : 1,
        },
        "Gerudo Membership Card" : {'quest.gerudos_card'             : True},
        "Stone of Agony"         : {'quest.stone_of_agony'           : True},
        "Zeldas Lullaby"         : {'quest.songs.zeldas_lullaby'     : True},
        "Eponas Song"            : {'quest.songs.eponas_song'        : True},
        "Sarias Song"            : {'quest.songs.sarias_song'        : True},
        "Suns Song"              : {'quest.songs.suns_song'          : True},
        "Song of Time"           : {'quest.songs.song_of_time'       : True},
        "Song of Storms"         : {'quest.songs.song_of_storms'     : True},
        "Minuet of Forest"       : {'quest.songs.minuet_of_forest'   : True},
        "Bolero of Fire"         : {'quest.songs.bolero_of_fire'     : True},
        "Serenade of Water"      : {'quest.songs.serenade_of_water'  : True},
        "Requiem of Spirit"      : {'quest.songs.requiem_of_spirit'  : True},
        "Nocturne of Shadow"     : {'quest.songs.nocturne_of_shadow' : True},
        "Prelude of Light"       : {'quest.songs.prelude_of_light'   : True},
        "Kokiri Emerald"         : {'quest.stones.kokiris_emerald'   : True},
        "Goron Ruby"             : {'quest.stones.gorons_ruby'       : True},
        "Zora Sapphire"          : {'quest.stones.zoras_sapphire'    : True},
        "Light Medallion"        : {'quest.medallions.light'         : True},
        "Forest Medallion"       : {'quest.medallions.forest'        : True},
        "Fire Medallion"         : {'quest.medallions.fire'          : True},
        "Water Medallion"        : {'quest.medallions.water'         : True},
        "Spirit Medallion"       : {'quest.medallions.spirit'        : True},
        "Shadow Medallion"       : {'quest.medallions.shadow'        : True},
        "Progressive Strength Upgrade" : {'upgrades.strength_upgrade' : None},
        "Progressive Scale"            : {'upgrades.diving_upgrade'   : None},
        "Progressive Wallet"           : {'upgrades.wallet'           : None},
        "Gold Skulltula Token" : {
            'quest.gold_skulltula'  : True,
            'gs_tokens'             : None,
        },
        "Double Defense" : {
            'double_defense'        : True,
            'defense_hearts'        : 20,
        },
        "Magic Meter" : {
            'magic_acquired'        : True,
            'magic'                 : [0x30, 0x60],
            'magic_level'           : None,
            'double_magic'          : [False, True],
        },
        "Rupee"                     : {'rupees' : None},
        "Rupees"                    : {'rupees' : None},
        "Rupee (Treasure Chest Game)" : {'rupees' : None},
        "Rupees (Treasure Chest Game)" : {'rupees' : None},
        "Magic Bean Pack" : {
            'item_slot.beans'       : 'beans',
            'ammo.beans'            : 10
        },
        "Ice Trap"                  : {'pending_freezes': None},
        "Triforce Piece"            : {'triforce_pieces': None},
        "Ocarina A Button"          : {'ocarina_buttons.a': True},
        "Ocarina C up Button"       : {'ocarina_buttons.c_up': True},
        "Ocarina C down Button"     : {'ocarina_buttons.c_down': True},
        "Ocarina C left Button"     : {'ocarina_buttons.c_left': True},
        "Ocarina C right Button"    : {'ocarina_buttons.c_right': True},
        "Boss Key (Forest Temple)"                : {'dungeon_items.forest.boss_key': True},
        "Boss Key (Fire Temple)"                  : {'dungeon_items.fire.boss_key': True},
        "Boss Key (Water Temple)"                 : {'dungeon_items.water.boss_key': True},
        "Boss Key (Spirit Temple)"                : {'dungeon_items.spirit.boss_key': True},
        "Boss Key (Shadow Temple)"                : {'dungeon_items.shadow.boss_key': True},
        "Boss Key (Ganons Castle)"                : {'dungeon_items.gt.boss_key': True},
        "Compass (Deku Tree)"                     : {'dungeon_items.deku.compass': True},
        "Compass (Dodongos Cavern)"               : {'dungeon_items.dodongo.compass': True},
        "Compass (Jabu Jabus Belly)"              : {'dungeon_items.jabu.compass': True},
        "Compass (Forest Temple)"                 : {'dungeon_items.forest.compass': True},
        "Compass (Fire Temple)"                   : {'dungeon_items.fire.compass': True},
        "Compass (Water Temple)"                  : {'dungeon_items.water.compass': True},
        "Compass (Spirit Temple)"                 : {'dungeon_items.spirit.compass': True},
        "Compass (Shadow Temple)"                 : {'dungeon_items.shadow.compass': True},
        "Compass (Bottom of the Well)"            : {'dungeon_items.botw.compass': True},
        "Compass (Ice Cavern)"                    : {'dungeon_items.ice.compass': True},
        "Map (Deku Tree)"                         : {'dungeon_items.deku.map': True},
        "Map (Dodongos Cavern)"                   : {'dungeon_items.dodongo.map': True},
        "Map (Jabu Jabus Belly)"                  : {'dungeon_items.jabu.map': True},
        "Map (Forest Temple)"                     : {'dungeon_items.forest.map': True},
        "Map (Fire Temple)"                       : {'dungeon_items.fire.map': True},
        "Map (Water Temple)"                      : {'dungeon_items.water.map': True},
        "Map (Spirit Temple)"                     : {'dungeon_items.spirit.map': True},
        "Map (Shadow Temple)"                     : {'dungeon_items.shadow.map': True},
        "Map (Bottom of the Well)"                : {'dungeon_items.botw.map': True},
        "Map (Ice Cavern)"                        : {'dungeon_items.ice.map': True},
        "Small Key (Forest Temple)"               : {
            'keys.forest': None,
            'total_keys.forest': None,
        },
        "Small Key (Fire Temple)"                 : {
            'keys.fire': None,
            'total_keys.fire': None,
        },
        "Small Key (Water Temple)"                : {
            'keys.water': None,
            'total_keys.water': None,
        },
        "Small Key (Spirit Temple)"               : {
            'keys.spirit': None,
            'total_keys.spirit': None,
        },
        "Small Key (Shadow Temple)"               : {
            'keys.shadow': None,
            'total_keys.shadow': None,
        },
        "Small Key (Bottom of the Well)"          : {
            'keys.botw': None,
            'total_keys.botw': None,
        },
        "Small Key (Gerudo Training Ground)"      : {
            'keys.gtg': None,
            'total_keys.gtg': None,
        },
        "Small Key (Thieves Hideout)"             : {
            'keys.fortress': None,
            'total_keys.fortress': None,
        },
        "Small Key (Ganons Castle)"               : {
            'keys.gc': None,
            'total_keys.gc': None,
        },
        "Small Key (Treasure Chest Game)"         : {
            'keys.tcg': None,
            'total_keys.tcg': None,
        },
        'Silver Rupee (Dodongos Cavern Staircase)':            {'silver_rupee_counts.dc_staircase': None},
        'Silver Rupee (Ice Cavern Spinning Scythe)':           {'silver_rupee_counts.ice_scythe': None},
        'Silver Rupee (Ice Cavern Push Block)':                {'silver_rupee_counts.ice_block': None},
        'Silver Rupee (Bottom of the Well Basement)':          {'silver_rupee_counts.botw_basement': None},
        'Silver Rupee (Shadow Temple Scythe Shortcut)':        {'silver_rupee_counts.shadow_scythe': None},
        'Silver Rupee (Shadow Temple Invisible Blades)':       {'silver_rupee_counts.shadow_blades': None},
        'Silver Rupee (Shadow Temple Huge Pit)':               {'silver_rupee_counts.shadow_pit': None},
        'Silver Rupee (Shadow Temple Invisible Spikes)':       {'silver_rupee_counts.shadow_spikes': None},
        'Silver Rupee (Gerudo Training Ground Slopes)':        {'silver_rupee_counts.gtg_slopes': None},
        'Silver Rupee (Gerudo Training Ground Lava)':          {'silver_rupee_counts.gtg_lava': None},
        'Silver Rupee (Gerudo Training Ground Water)':         {'silver_rupee_counts.gtg_water': None},
        'Silver Rupee (Spirit Temple Child Early Torches)':    {'silver_rupee_counts.spirit_torches': None},
        'Silver Rupee (Spirit Temple Adult Boulders)':         {'silver_rupee_counts.spirit_boulders': None},
        'Silver Rupee (Spirit Temple Lobby and Lower Adult)':  {'silver_rupee_counts.spirit_lobby': None},
        'Silver Rupee (Spirit Temple Sun Block)':              {'silver_rupee_counts.spirit_sun': None},
        'Silver Rupee (Spirit Temple Adult Climb)':            {'silver_rupee_counts.spirit_adult_climb': None},
        'Silver Rupee (Ganons Castle Spirit Trial)':           {'silver_rupee_counts.trials_spirit': None},
        'Silver Rupee (Ganons Castle Light Trial)':            {'silver_rupee_counts.trials_light': None},
        'Silver Rupee (Ganons Castle Fire Trial)':             {'silver_rupee_counts.trials_fire': None},
        'Silver Rupee (Ganons Castle Shadow Trial)':           {'silver_rupee_counts.trials_shadow': None},
        'Silver Rupee (Ganons Castle Water Trial)':            {'silver_rupee_counts.trials_water': None},
        'Silver Rupee (Ganons Castle Forest Trial)':           {'silver_rupee_counts.trials_forest': None},
        # HACK: these counts aren't used since exact counts based on whether the dungeon is MQ are defined above,
        # but the entries need to be there for key rings to be valid starting items
        "Small Key Ring (Forest Temple)"          : {
            'keys.forest': 6,
            'total_keys.forest': 6,
        },
        "Small Key Ring (Fire Temple)"            : {
            'keys.fire': 8,
            'total_keys.fire': 8,
        },
        "Small Key Ring (Water Temple)"           : {
            'keys.water': 6,
            'total_keys.water': 6,
        },
        "Small Key Ring (Spirit Temple)"          : {
            'keys.spirit': 7,
            'total_keys.spirit': 7,
        },
        "Small Key Ring (Shadow Temple)"          : {
            'keys.shadow': 6,
            'total_keys.shadow': 6,
        },
        "Small Key Ring (Bottom of the Well)"     : {
            'keys.botw': 3,
            'total_keys.botw': 3,
        },
        "Small Key Ring (Gerudo Training Ground)" : {
            'keys.gtg': 9,
            'total_keys.gtg': 9,
        },
        "Small Key Ring (Thieves Hideout)"        : {
            'keys.fortress': 4,
            'total_keys.fortress': 4,
        },
        "Small Key Ring (Ganons Castle)"          : {
            'keys.gc': 3,
            'total_keys.gc': 3,
        },
        "Small Key Ring (Treasure Chest Game)"    : {
            'keys.tcg': 6,
            'total_keys.tcg': 6,
        },
        'Silver Rupee Pouch (Dodongos Cavern Staircase)':            {'silver_rupee_counts.dc_staircase': 5},
        'Silver Rupee Pouch (Ice Cavern Spinning Scythe)':           {'silver_rupee_counts.ice_scythe': 5},
        'Silver Rupee Pouch (Ice Cavern Push Block)':                {'silver_rupee_counts.ice_block': 5},
        'Silver Rupee Pouch (Bottom of the Well Basement)':          {'silver_rupee_counts.botw_basement': 5},
        'Silver Rupee Pouch (Shadow Temple Scythe Shortcut)':        {'silver_rupee_counts.shadow_scythe': 5},
        'Silver Rupee Pouch (Shadow Temple Invisible Blades)':       {'silver_rupee_counts.shadow_blades': 10},
        'Silver Rupee Pouch (Shadow Temple Huge Pit)':               {'silver_rupee_counts.shadow_pit': 5},
        'Silver Rupee Pouch (Shadow Temple Invisible Spikes)':       {'silver_rupee_counts.shadow_spikes': 10},
        'Silver Rupee Pouch (Gerudo Training Ground Slopes)':        {'silver_rupee_counts.gtg_slopes': 5},
        'Silver Rupee Pouch (Gerudo Training Ground Lava)':          {'silver_rupee_counts.gtg_lava': 6},
        'Silver Rupee Pouch (Gerudo Training Ground Water)':         {'silver_rupee_counts.gtg_water': 5},
        'Silver Rupee Pouch (Spirit Temple Child Early Torches)':    {'silver_rupee_counts.spirit_torches': 5},
        'Silver Rupee Pouch (Spirit Temple Adult Boulders)':         {'silver_rupee_counts.spirit_boulders': 5},
        'Silver Rupee Pouch (Spirit Temple Lobby and Lower Adult)':  {'silver_rupee_counts.spirit_lobby': 5},
        'Silver Rupee Pouch (Spirit Temple Sun Block)':              {'silver_rupee_counts.spirit_sun': 5},
        'Silver Rupee Pouch (Spirit Temple Adult Climb)':            {'silver_rupee_counts.spirit_adult_climb': 5},
        'Silver Rupee Pouch (Ganons Castle Spirit Trial)':           {'silver_rupee_counts.trials_spirit': 5},
        'Silver Rupee Pouch (Ganons Castle Light Trial)':            {'silver_rupee_counts.trials_light': 5},
        'Silver Rupee Pouch (Ganons Castle Fire Trial)':             {'silver_rupee_counts.trials_fire': 5},
        'Silver Rupee Pouch (Ganons Castle Shadow Trial)':           {'silver_rupee_counts.trials_shadow': 5},
        'Silver Rupee Pouch (Ganons Castle Water Trial)':            {'silver_rupee_counts.trials_water': 5},
        'Silver Rupee Pouch (Ganons Castle Forest Trial)':           {'silver_rupee_counts.trials_forest': 5},
    }

    equipable_items: dict[str, dict[str, list[str]]] = {
        'equips_adult' : {
            'items': [
                'hookshot',
                'hammer',
                'bomb',
                'bow',
                'nut',
                'lens',
                'farores_wind',
                'dins_fire',
                'bombchu',
                'nayrus_love',
                'adult_trade',
                'bottle_1',
                'bottle_2',
                'bottle_3',
                'bottle_4',
            ],
            'sword' : [
                'biggoron_sword',
                'master_sword',
            ],
            'shield' : [
                'mirror_shield',
                'hylian_shield',
            ],
            'tunic' : [
                'goron_tunic',
                'zora_tunic',
                'kokiri_tunic',
            ],
            'boots' : [
                'kokiri_boots'
            ],
        },
        'equips_child' : {
            'items': [
                'bomb',
                'boomerang',
                'slingshot',
                'stick',
                'nut',
                'lens',
                'farores_wind',
                'dins_fire',
                'bombchu',
                'nayrus_love',
                'beans',
                'bottle_1',
                'bottle_2',
                'bottle_3',
                'bottle_4',
            ],
            'sword' : [
                'kokiri_sword',
            ],
            'shield' : [
                'deku_shield',
                'hylian_shield',
            ],
            'tunic' : [
                'kokiri_tunic',
            ],
            'boots' : [
                'kokiri_boots',
            ],
        }
    }
