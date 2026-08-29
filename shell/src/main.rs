use pf_ports::{Clock, LaunchRequest, LaunchResult, MonotonicTime, SessionEvent};
use pf_session_authority::{
    Authority, AuthorityApi as _, FailureRung, MemoryStore, Observation, SessionSystem,
};
use std::time::Duration;
use std::{cell::Cell, rc::Rc};

#[derive(Clone, Default)]
struct SharedClock(Rc<Cell<u64>>);
impl SharedClock {
    fn advance(&self, duration: Duration) {
        self.0.set(self.0.get().saturating_add(duration.as_nanos() as u64));
    }
}
impl Clock for SharedClock {
    fn now(&self) -> MonotonicTime { MonotonicTime::from_nanos(self.0.get()) }
}

#[derive(Default)]
struct CooperativeSystem;
impl SessionSystem for CooperativeSystem {
    fn start_foreground(&mut self, _: &LaunchRequest, _: &str) -> Result<bool, String> { Ok(true) }
    fn request_graceful_stop(&mut self, _: &str) -> Result<(), String> { Ok(()) }
    fn enforce_termination(&mut self, _: &str) -> Result<(), String> { Ok(()) }
    fn activate_selected_owner(&mut self) -> Result<(), String> { Ok(()) }
}

fn line(case: &str, kind: &str, value: &str) {
    println!("case={case} kind={kind} value={value}");
}

fn main() -> Result<(), String> {
    let case = std::env::args().nth(1).ok_or("missing modeled case")?;
    let clock = SharedClock::default();
    let mut authority = Authority::open(
        MemoryStore::default(), CooperativeSystem, clock.clone(), 4, Duration::from_millis(10),
    ).map_err(|e| format!("{e:?}"))?;
    let LaunchResult::Accepted { .. } = authority.launch(LaunchRequest { item_id: "stand-in".into() })
        .map_err(|e| format!("{e:?}"))? else { return Err("launch rejected".into()) };
    authority.observe(Observation::SessionRunning).map_err(|e| format!("{e:?}"))?;
    line(&case, "frame", "app");
    match case.as_str() {
        "graceful" => {
            authority.intake_safe_return().map_err(|e| format!("{e:?}"))?;
            line(&case, "protected-intake", "safe-return");
            authority.tick().map_err(|e| format!("{e:?}"))?;
            authority.observe(Observation::UnitInactive).map_err(|e| format!("{e:?}"))?;
        }
        "forced" => {
            authority.intake_safe_return().map_err(|e| format!("{e:?}"))?;
            line(&case, "protected-intake", "safe-return");
            authority.tick().map_err(|e| format!("{e:?}"))?;
            clock.advance(Duration::from_millis(10));
            authority.tick().map_err(|e| format!("{e:?}"))?;
            authority.observe(Observation::UnitInactive).map_err(|e| format!("{e:?}"))?;
        }
        "crash" => {
            authority.observe(Observation::SessionCrashed { summary: "modeled-crash".into() })
                .map_err(|e| format!("{e:?}"))?;
            authority.observe(Observation::UnitInactive).map_err(|e| format!("{e:?}"))?;
        }
        "recovery" => {
            authority.observe(Observation::Failed { rung: FailureRung::Termination, reason: "modeled-fault".into() })
                .map_err(|e| format!("{e:?}"))?;
            line(&case, "event", "RecoveryRequired");
            return Ok(());
        }
        _ => return Err(format!("unknown modeled case: {case}")),
    }
    authority.observe(Observation::TargetReleased).map_err(|e| format!("{e:?}"))?;
    authority.observe(Observation::SelectedOwnerActive).map_err(|e| format!("{e:?}"))?;
    line(&case, "ack", "presentation");
    authority.observe(Observation::PresentationAcknowledged).map_err(|e| format!("{e:?}"))?;
    for (_, event) in authority.events_for("sim") {
        match event {
            SessionEvent::Terminal(receipt) => line(&case, "receipt", &format!("{receipt:?}")),
            _ => {}
        }
    }
    line(&case, "frame", "shell");
    Ok(())
}
