// Be able to use cool `|` syntax in matches.
#![feature(or_patterns)]

extern crate midir;
extern crate tokio;

use tokio::stream::{StreamExt, StreamMap};

use crate::SysexController;

#[tokio::main]
async fn main() {
    let mut controllers = FireController::attach_to_all();

    let mut map = StreamMap::new();

    for (i, c) in controllers.iter_mut().enumerate() {
        c.set_color_cube();
        c.update_leds();

        if let Some(rx) = c.event_rx.take() {
            map.insert(i, rx);
        }
    }

    while let Some((i, evt)) = map.next().await {
        let c = controllers.get_mut(i).unwrap();
        match evt {
            ControllerEvent::GridButton(idx, _, _, ButtonState::Down, _) => {
                c.set_led(idx, 0x7f, 0x7f, 0x7f);
                c.update_leds();
            },
            ControllerEvent::GridButton(idx, _, _, ButtonState::Up, _) => {
                c.set_led(idx, 0, 0, 0);
                c.update_leds();
            },
            _ => ()
        }
    }

    ()
}
