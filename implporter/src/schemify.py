from pdfminer.layout import LTTextContainer, LTChar
from pdfminer.high_level import extract_pages
import json
import re

def get_container_info(elem):
    for text_line in elem:
        if isinstance(text_line, LTChar):
            text_line = [text_line]
        for char in text_line:
            if isinstance(char, LTChar):
                return (char.fontname, char.size)
    return (None, None)


def get_tag_from_size(siz, config, error_margin=0.3):
    for k, v in config['sizes'].items():
        if abs(k - siz) < error_margin:
            return v

MIDI_REF_CONFIG = {
    "pdf": "doc-inputs/jupx-midi-ref.pdf",
    "output_map": "../sysex-maps/jupx.json",
    "sizes": {
        21: "title",
        14: "h1", # ex: "1. Data Reception"
        12: "h2", # ex: "Channel Voice Messages"
        10: "h3", # ex: "Note Off" with bullet.
        6: "text",
    },
    "margins": {
        "top": 784, # compared against y0
        "bottom": 29, # compared against y1 
    },
    "key_sections": {
        "h1": [
            "3. Parameter Address Map",
        ]
    },
    "port_names": [
        "JUPITER-X"
    ],
    "ignore_port_names": [
        "JUPITER-X JUPITER-X DAW CTRL"
    ],
}

#### Table Parsing


RE_SNIFF_TABLE_HEADER = re.compile("^\* \[(.+)\]$")
RE_SNIFF_TABLE_ROW_SEP = re.compile("^[|+]--")
RE_SNIFF_VAL_TABLE = re.compile("^\|([#]?) +")

RE_TABLE_OPEN_CLOSE = re.compile("^\+\-+\+$")
# headers are always 2 columns
RE_TABLE_HEADER_ROW = re.compile("^\| +([^|]+) +\| +([^|]+) +\|$")

RE_TYPE_TABLE_INTER = re.compile("^\|-+\+-+\|$")
RE_TYPE_TABLE_ADDR_ROW = re.compile("^\| +((?:[0-9a-fA-F]{2} ?)+) \| ([^|]+) +(?:\[([^|]+)\])? \|$")
RE_TYPE_TABLE_ELLIPSIS = re.compile("^\| +: +\| +\|$")

RE_VAL_TABLE_DEF_ROW = re.compile("^\|([#]?) +((?:[0-9a-fA-F]{2} ?)+) \| ([0a-z]{4} [0a-z]{4}) \| ([^|]+)  \(([^)]+)\) \|$")
RE_VAL_TABLE_MULTI_BYTE_ROW = re.compile("^\|([#]?) +((?:[0-9a-fA-F]{2} ?)+) \| ([0a-z]{4} [0a-z]{4}) \| +\|$")
RE_VAL_TABLE_VALUES_ROW = re.compile("^\| +\| +\| +([^\|]+)\|$")
RE_VAL_TABLE_TOTAL = re.compile("^\| +((?:[0-9a-fA-F]{2} ?)+) \|Total Size +\|$")

RE_ONE_STRIP_MID = re.compile(" \(0+1\) ")
RE_ONE_STRIP_END = re.compile("(?: 1)|(?: ?\(0*1\))$")

RE_STRAY_PAGE_NUMBER = re.compile("^\d+$")

# Values that don't make sense on the Jupiter-Xm get starred like this, which
# also ends up paired with a footnote after each table that gets discarded.
RE_VAL_STRIP_OPT_STAR = re.compile("^\(\*\)")

def parse_hex_offset(text):
    bytes = text.split(" ")
    val = 0
    for byte_str in bytes:
        val = val << 8 | int(byte_str, base=16)
    return val

def parse_bitmask(text):
    """
    Parse bitmasks like so:
    - "0000 aaaa" (ridx: 3) => 0xf
    - "0aaa aaaa" (ridx: 0) => 0x7f
    - "0000 bbbb" (ridx: 3) => 0xf
    """
    text = text.replace(" ", "")
    last_zero = "".rfind("0")
    p2 = 1 << (8 - last_zero)
    return p2 - 1

def parse_num(str):
    # Handle weird "L64 - 63R" panning case by mapping it to "-64 - 63"
    if str[0] == "L":
        return -int(str[1:])
    elif str[-1] == "R":
        return int(str[0:-1])
    # Explicitly parse floats as floats
    elif "." in str:
        return float(str)
    # And assume anything leftover is an int
    else:
        return int(str)

def grok_midi_table(lines):
    """
    Given a newline-delimited hunk of text corresponding to an ASCII table as
    found in recent Roland MIDI reference docs, process it into a dictionary
    with payload:
    - "header": Array of head column strings, or None if there was no header
      and this is therefore probably a continuation of a previous table.
    - "rows": Array of dictionaries of the form:
      - "first_offset_start": Numeric offset of the first sub-row.
      - "last_offset_start": Numeric offset of the last sub-row, or if there
        are no sub-rows, the same as the "first_offset_start".
      - "name"
      - "type"
      - "values"
    """
    result = {}

    if RE_TABLE_OPEN_CLOSE.match(lines[0]):
        lines = lines[1:]
        header_pieces = [[], []]
        for i_line in range(len(lines)):
            m_header = RE_TABLE_HEADER_ROW.match(lines[i_line])
            if m_header:
                col_0 = m_header.group(1).strip()
                if col_0:
                    header_pieces[0].append(col_0)
                col_1 = m_header.group(2).strip()
                if col_1:
                    header_pieces[1].append(col_1)
            else:
                lines = lines[i_line + 1:]
                break
        header_values = [" ".join(header_pieces[0]), " ".join(header_pieces[1])]
        result["header"] = header_values
    else:
        result["header"] = None

    rows = result["rows"] = []

    pending_row = None
    def flush_row():
        nonlocal pending_row
        if pending_row is None:
            return

        rows.append(pending_row)
        pending_row = None
    
    def ensure_value_row(addr, bitmask):
        nonlocal pending_row
        if pending_row is None:
            pending_row = {
                "first_offset_start": addr,
                "last_offset_start": addr,
                "name": None,
                "kind": "value",
                "bitmask": bitmask,
                "discrete_range": None,
                "human_values": "",
            }
        else:
            pending_row["last_offset_start"] = addr
            if pending_row["bitmask"] != bitmask:
                print("Bitmask mismatch!", pending_row["bitmask"], bitmask)

    table_open = True
    for line in lines:
        if RE_TABLE_OPEN_CLOSE.match(line):
            table_open = False
            continue
        
        if not table_open:
            print("TABLE CLOSED BUT GOT:", line)
        
        if RE_TYPE_TABLE_INTER.match(line):
            flush_row()
            continue

        # -- Type Table
        m_addr_row = RE_TYPE_TABLE_ADDR_ROW.match(line)
        if m_addr_row:
            addr = parse_hex_offset(m_addr_row.group(1))
            raw_desc = m_addr_row.group(2).strip()
            desc = RE_ONE_STRIP_MID.sub(" ", raw_desc)
            desc = RE_ONE_STRIP_END.sub("", desc)
            type = m_addr_row.group(3)

            if not pending_row:
                pending_row = {
                    "first_offset_start": addr,
                    "last_offset_start": addr,
                    "name": desc,
                    "kind": "type",
                    "type": type,
                    "stride": None,
                }
            else:
                pending_row["last_offset_start"] = addr
                if pending_row["stride"] is None:
                    pending_row["stride"] = addr - pending_row["first_offset_start"]
            continue
    
        if RE_TYPE_TABLE_ELLIPSIS.match(line):
            # The pseudo-ellipsis case of ":" should just be skipped.
            continue

        # -- Value Table
        m_multi = RE_VAL_TABLE_MULTI_BYTE_ROW.match(line)
        if m_multi:
            addr = parse_hex_offset(m_multi.group(2))
            bitmask = parse_bitmask(m_multi.group(3))
            # Multi-byte row is a bitmask row without a definition, and
            # will have a "#" to indicate the start.
            if m_multi.group(1) == "#":
                flush_row()
            ensure_value_row(addr, bitmask)
            continue

        m_def = RE_VAL_TABLE_DEF_ROW.match(line)
        if m_def:
            addr = parse_hex_offset(m_def.group(2))
            bitmask = parse_bitmask(m_def.group(3))
            desc = m_def.group(4).strip()
            paren_range = m_def.group(5)

            if pending_row is not None and pending_row["name"] is not None:
                flush_row()

            ensure_value_row(addr, bitmask)
            pending_row["name"] = RE_VAL_STRIP_OPT_STAR.sub("", desc)
            pending_row["discrete_range"] = paren_range
            continue

        m_vals_row = RE_VAL_TABLE_VALUES_ROW.match(line)
        if m_vals_row:
            pending_row["human_values"] += m_vals_row.group(1).strip()
            continue

        m_total_row = RE_VAL_TABLE_TOTAL.match(line)
        if m_total_row:
            result["total"] = parse_hex_offset(m_total_row.group(1))
            continue

        # pdfminer seems to be folding page numbers into the table container
        # when the table continues directly to the bottom of the page.
        #
        # We could probably filter these out since the font size is distinct,
        # but it's easy enough to just notice them via regexp here.
        if RE_STRAY_PAGE_NUMBER.match(line):
            continue
        
        print("UNKNOWN TABLE ROW FORMAT:", line)
    
    flush_row()
    return result

class MapMaker(object):
    """
    Process a series of configuration files (currently hardcoded) in order
    to know hot to process PDF files to build up a sysex map and potentially
    attached details and metadata.

    This is not remotely intended to be a fully generic mechanism.  The key
    simplifying constraint is that we're assuming we are only dealing with
    the Roland ZenCore product line, which should have similar looking PDF
    files for the various synthesizers.  Any substantially different PDF
    specifications should get their own implementations.

    Currently the key things end up being:
    - Knowing margins to ignore the header/footer from the page.
    - Knowing font size mappings to know interesting headers versus the body
      payloads.
    - Stateful tracking of whether there's an active table or not and stitching
      together fragments of tables into a single block of text to process.
      This is necessary because the tables spill into subsequent columns/pages
      without any regard for table semantics, so the parser loses too much
      info.
    """
    def __init__(self, configs):
        self.configs = configs
        self.type_chunks_by_type = {}
        self.value_chunks_by_type = {}
        self.sizes_by_type = {}
        self.pending_table_type = None
        self.pending_table_lines = None
        self.pending_table_size = None

    def consider_text(self, text):
        # print("** CONSIDERING:", text)
        # In some cases there's some weird leading whitespace for the tables,
        # let's get rid of that to avoid contaminating the regexps.  But we
        # just want to eat a single space, not strip everything.
        if len(text) <= 1:
            return
        if text[0] == " " and (text[1] == "+" or text[1] == "|"):
            text = text[1:]

        if RE_SNIFF_TABLE_HEADER.match(text):
            self.handle_table_header(text)
        elif RE_SNIFF_TABLE_ROW_SEP.match(text) or \
             RE_SNIFF_VAL_TABLE.match(text):
            self.handle_table(text)
        else:
            # maybe we need to split the text in two sets of lines
            if '\n' in text:
                h, t = text.split('\n', 1)
                self.consider_text(h)
                self.consider_text(t)

    def process_table(self, type, table_info):
        print("midi table:", type, "\n", json.dumps(table_info, indent=2))
        
        if not len(table_info["rows"]):
            return
        
        table_kind = table_info["rows"][0]["kind"]
        if table_kind == "type":
            self.process_type_table(type, table_info)
        else:
            self.process_value_table(type, table_info)
    
    def process_type_table(self, type, table_info):

        json_rows = self.type_chunks_by_type.get(type, [])
        self.type_chunks_by_type[type] = json_rows
        for row in table_info["rows"]:
            json_row = {
                "name": row["name"],
                "first_offset_start": row["first_offset_start"],
                "last_offset_start": row["last_offset_start"],
                "type": row["type"],
            }
            if row["stride"] is not None:
                json_row["stride"] = row["stride"]

            json_rows.append(json_row)

    def process_value_table(self, type, table_info):
        if 'total' in table_info:
            self.sizes_by_type[type] = table_info['total']
            print("set pending table size", table_info['total'])
        json_rows = self.value_chunks_by_type.get(type, [])
        self.value_chunks_by_type[type] = json_rows
        for row in table_info["rows"]:
            low, high = [parse_num(x) for x in row["discrete_range"].split(" - ")]

            json_row = {
                "name": row["name"],
                "first_offset_start": row["first_offset_start"],
                "last_offset_start": row["last_offset_start"],
                "bitmask": row["bitmask"],
                "discrete_range_low": low,
                "discrete_range_high": high,
            }

            hvals = row["human_values"]
            total_size = row
            # Extract units if present
            idx_brace_open = hvals.rfind("[")
            if idx_brace_open != -1:
                json_row["human_value_units"] = hvals[idx_brace_open+1:-1]
                hvals = hvals[:idx_brace_open]
            
            if "," in hvals:
                json_row["human_value_list"] = [x.strip() for x in hvals.split(",")]
            elif "-" in hvals:
                # XXX Actually, values are frequently represented as floats, but
                # I'm not sure I actually saw any fractional values, so it's
                # easiest to just stick with an int for now.
                h_low, h_high = [parse_num(x) for x in hvals.split(" - ")]
                json_row["human_value_base"] = h_low
            else:
                # This should probably be an empty string then.
                if hvals != "":
                    print("WARN: weird hval of", hvals)

            json_rows.append(json_row)

    def flush_table(self):
        table_info = grok_midi_table(self.pending_table_lines)
        self.process_table(self.pending_table_type, table_info)

        self.pending_table_type = None
        self.pending_table_lines = None
        self.pending_table_size = None

    def handle_table_header(self, text):
        if self.pending_table_lines:
            self.flush_table()

        m = RE_SNIFF_TABLE_HEADER.match(text)
        type = m.group(1)
        self.pending_table_type = type
        print("Type:", type)

    def handle_table(self, text):
        lines = text.splitlines()

        start_from = None
        if self.pending_table_lines is not None:
            start_from = 0
        else:
            self.pending_table_lines = []
            start_from = 1
            if not RE_TABLE_OPEN_CLOSE.match(lines[0]):
                print("WEIRD: Start of a table without a table?\n", text)
        
        found_end = False
        for i_line in range(start_from, len(lines)):
            line = lines[i_line]
            if RE_TABLE_OPEN_CLOSE.match(line):
                # we are discarding lines[i_line+1:]
                #
                # We print out what we're discarding for sanity checking of
                # this process.
                if i_line < (len(lines) - 1):
                    print("DISCARDING\n", "  ", "\n  ".join(lines[i_line+1:]))
                found_end = True
                lines = lines[0:i_line + 1]
                break
        
        self.pending_table_lines.extend(lines)
        if found_end:
            self.flush_table()

    def prepare_for_config(self, config):
        self.pending_table_type = "ROOT"
        self.pending_table_lines = None

        self.type_chunks_by_type = {}
        self.value_chunks_by_type = {}

    def process_config(self, config):
        top_margin = config["margins"]["top"]
        bottom_margin = config["margins"]["bottom"]
        for page_layout in extract_pages(config["pdf"]):
            stuff_in_page = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    # ignore page details in the margins (title, page numbers)
                    if element.y0 >= top_margin or element.y1 <= bottom_margin:
                        continue

                    # attempt to map boxes based on the size of their contents
                    (fontname, size) = get_container_info(element)
                    tag = get_tag_from_size(size, config)
                    if tag is None:
                        print(f"skipping unknown stuff of size {size} and font {fontname}", element)
                        continue

                    # Show progress.
                    tx = element.get_text()
                    if tag != "text":
                        #print("page", page_layout.pageid, "font", fontname, "size", size, "bbox", element.bbox)
                        print(tag, tx)
                        continue

                    # If we think the text is in the 2nd column, effectively add
                    # a y offset of the entire first column's height.
                    col_boost = 0
                    if element.x0 >= 300:
                        col_boost = top_margin

                    stuff_in_page.append({
                        # we want the sort order to assume 2 columns, placing
                        # things in order of scanning down the first column,
                        # then the second.
                        "sort_key": (top_margin - element.y0) + col_boost,
                        "text": tx,
                    })
                    
            stuff_in_page.sort(key=lambda x: x["sort_key"])
            for thing in stuff_in_page:
                self.consider_text(thing["text"])
    
    def finish_config(self, config):
        aggr_dict = {
            "port_names": config["port_names"],
            "ignore_port_names": config["ignore_port_names"],
            "type_entries": self.type_chunks_by_type,
            "value_entries": self.value_chunks_by_type,
            "size_of_types": self.sizes_by_type,
        }

        with open(config["output_map"], "w") as f:
            json.dump(aggr_dict, f, indent=2)

    def process_all(self):
        for config in self.configs:
            self.prepare_for_config(config)
            self.process_config(config)
            self.finish_config(config)

if __name__ == "__main__":
    maker = MapMaker([MIDI_REF_CONFIG])
    maker.process_all()