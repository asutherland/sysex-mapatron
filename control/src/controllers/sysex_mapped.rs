use midir::{MidiInput, MidiInputConnection, MidiOutput, MidiOutputConnection};
use serde::{Deserialize, Serialize};
use serde_json::Result;

use std::cmp::{Eq, PartialEq, min};
use std::hash::{Hash, Hasher};
use tokio::stream::{StreamExt, StreamMap};
use tokio::sync::mpsc;

#[derive(Serialize, Deserialize)]
struct SysexMapTypeEntry {
    name: String,
    first_offset_start: uint32_t,
    last_offset_start: uint32_t,
    type: String,
    stride: Maybe<uint32_t>,
}

#[derive(Serialize, Deserialize)]
struct SysexMapValueEntry {
    name: String,
    first_offset_start: uint32_t,
    last_offset_start: uint32_t,
    bitmask: uint32_t,
    discrete_range_low: uint32_t,
    discrete_range_high: uint32_t,
    human_value_list: Maybe<Vec<String>>,
    human_value_base: Maybe<int32_t>,
    human_value_units: Maybe<String>,
}

#[derive(Serialize, Deserialize)]
struct SysexMap {
    port_names: Vec<String>,
    ignore_port_names: Vec<String>,
    type_entries: BTreeMap<String, SysexMapTypeEntry>,
    value_entries: BTreeMap<String, SysexMapValueEntry>,
}

struct ConnectedController {
    in_conn: MidiInputConnection<()>,
    out_conn: MidiOutputConnection,
}

enum ControllerState {
    Disconnected,
    Connected(ConnectedController),
}

pub struct Controller {
    /// Identifier for the controller.  Ideally this would be the serial number
    /// of the device extracted via sysex or the USB path to the device.  Right
    /// now it's just a one-up.
    id: u32,
    state: ControllerState,
    event_rx: Option<mpsc::Receiver<ControllerEvent>>,

    // 7 header bytes + (4 bytes per grid led * 64 leds) + 1 end byte.
    led_msg_buf: [u8; 7 + 4 * 64 + 1],
}


impl Controller {
    /// Finds all Fire controllers on the system and returns them in a vector.
    pub fn attach_to_all(config_path: &str) -> Vec<Controller> {
        let mut controllers: Vec<Controller> = vec![];

        // We iterate over all input ports and for those that match the prefix,
        // we find the exact matching output port.  The ownership model is that
        // calling connect() on a MidiInput consumes (moves) it, so we do a
        // pass to figure out the port names we want, and then a pass that
        // creates MidiInput and MidiOutput instances to connect to that
        // specific instance.

        let walk_in = MidiInput::new("Fire-Walk").unwrap();
        // Accumulate the list of ports completely first so there's no overlap
        // of MidiInput lifetimes.
        let desired_names : Vec<String> = walk_in.ports().into_iter().filter_map(|p| {
            let name = walk_in.port_name(&p).unwrap();
            if name.starts_with(MIDI_INPUT_PORT_PREFIX) {
                Some(name)
            } else {
                None
            }
        }).collect();

        for (i, desired_name) in desired_names.into_iter().enumerate() {
            let midi_in = MidiInput::new("Fire-Walk").unwrap();
            let midi_out = MidiOutput::new("Fire").unwrap();

            let (mut tx, mut rx) = mpsc::channel::<ControllerEvent>(100);

            let in_port = midi_in.ports().into_iter().find_map(|p| {
                if midi_in.port_name(&p).unwrap() == desired_name {
                    Some(p)
                } else {
                    None
                }
            }).unwrap();
            let in_conn = midi_in.connect(
                &in_port, "fire-in", move |_stamp, msg, _| {
                    if let Some(event) = ControllerEvent::from_midi(msg) {
                        tx.try_send(event).expect("Send exploded");
                    }
                }, ()).unwrap();

            // The out port should have the same name as the in name.
            let out_port = midi_out.ports().into_iter().find_map(|p| {
                if midi_out.port_name(&p).unwrap() == desired_name {
                    Some(p)
                } else {
                    None
                }
            }).unwrap();
            let out_conn = midi_out.connect(&out_port, "fire-out").unwrap();

            let mut controller = Controller {
                id: i as u32,
                state: ControllerState::Connected(ConnectedController {
                    in_conn,
                    out_conn,
                }),
                event_rx: Some(rx),
                led_msg_buf: [0; 264],
            };
            controller.init();
            controllers.push(controller);
        }

        controllers
    }

    /// Initializes any pre-allocated buffers.
    fn init(&mut self) {
        let len: u16 = 4 * 64;
        self.led_msg_buf[0..7].copy_from_slice(
            &[0xf0, 0x47, 0x7f, 0x43, 0x65, ((len >> 7)&0x7f) as u8, (len&0x7f) as u8]);

        // The first byte of each 4-byte tuple is the index of the button to
        // update.
        for i in 0..64 {
            self.led_msg_buf[7 + i * 4] = i as u8;
        }
        self.led_msg_buf[self.led_msg_buf.len() - 1] = 0xf7;
    }


    /// Do a basic 4x4 color cube cut into 4 slices.
    pub fn set_color_cube(&mut self) {
        for i in 0..64 {
            let x: u8 = i % 4;
            let y: u8 = i / 16;
            let z: u8 = (i % 16) / 4;
            self.led_msg_buf[7 + (i as usize) * 4 + 1] = min(0x7f, x * 0x20);
            self.led_msg_buf[7 + (i as usize) * 4 + 2] = min(0x7f, y * 0x20);
            self.led_msg_buf[7 + (i as usize) * 4 + 3] = min(0x7f, z * 0x20);
        }
    }

    pub fn set_led(&mut self, i: u8, r: u8, g: u8, b: u8) {
        self.led_msg_buf[7 + (i as usize) * 4 + 1] = min(0x7f, r);
        self.led_msg_buf[7 + (i as usize) * 4 + 2] = min(0x7f, g);
        self.led_msg_buf[7 + (i as usize) * 4 + 3] = min(0x7f, b);
    }

    pub fn update_leds(&mut self) {
        if let ControllerState::Connected(cs) = &mut self.state {
            cs.out_conn.send(&self.led_msg_buf).unwrap();
        }
    }
}

impl Hash for Controller {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.id.hash(state);
    }
}

impl Eq for Controller {}

impl PartialEq for Controller {
    fn eq(&self, other: &Self) -> bool {
        self.id == other.id
    }
}
