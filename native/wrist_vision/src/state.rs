use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ChatRow {
    pub id: String,
    pub role: String,
    pub text: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Chat,
    Framing,
    Busy,
    Result,
}

pub struct InputGate {
    pressed: bool,
    armed: bool,
}
impl Default for InputGate {
    fn default() -> Self {
        Self {
            pressed: true,
            armed: false,
        }
    }
}
impl InputGate {
    pub fn edge(&mut self, pressed: bool) -> bool {
        if !pressed {
            self.armed = true;
        }
        let edge = pressed && !self.pressed && self.armed;
        self.pressed = pressed;
        edge
    }
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn held_trigger_cannot_double_confirm_or_click_on_start() {
        let mut gate = InputGate::default();
        assert!(!gate.edge(true));
        assert!(!gate.edge(false));
        assert!(gate.edge(true));
        assert!(!gate.edge(true));
        gate.reset();
        assert!(!gate.edge(true));
    }
}
