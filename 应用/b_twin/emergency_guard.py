"""Sparse, route-aware yielding; old emergency experiments stay frozen."""
# AI-assisted: OpenAI Codex; retain actual usage and human review for submission.
from __future__ import annotations

import csv
import json
import time

from .emergency import EmergencyController, prepare_emergency, VEHICLE
from .runner import run_traci, digest
from .evaluation import evaluate

GUARD = {
    "version": "guarded_yield_v1", "lookahead_m": 60.,
    "maximum_task_speed_mps": 5., "maximum_leader_speed_mps": 5.,
    "junction_margin_m": 25., "route_lookahead_m": 120.,
    "minimum_gap_m": 3., "gap_headway_s": 1.5,
    "per_vehicle_cooldown_s": 30., "maximum_requests_per_vehicle_edge": 1,
    "response_observation_s": 4., "eta_floor_speed_mps": 3.,
    "green_arrival_buffer_s": 2.,
}
POLICIES = {"baseline", "guarded", "guarded_green"}


def lane_continues(lane, next_edges, vehicle_class, remaining_m, horizon_m):
    """Require lane connections, including the immediate next route edge.

    No hypothetical lateral move is used to justify the target lane. Looking
    beyond a single edge avoids pushing a through vehicle into a short exit.
    """
    frontier = [lane]
    covered = remaining_m
    checked = []
    for edge_id in next_edges:
        reachable = {connection.getToLane() for current in frontier
            for connection in current.getOutgoing()
            if connection.getToLane().getEdge().getID() == edge_id
            and connection.getToLane().allows(vehicle_class)}
        checked.append(edge_id)
        if not reachable:
            return False, checked
        frontier = list(reachable)
        covered += min(candidate.getLength() for candidate in frontier)
        if covered >= horizon_m:
            break
    return True, checked


def space_check(position, length, speed, target_vehicles, parameters=GUARD):
    """Conservative measured front/rear space; SUMO safety checks still apply."""
    ahead = [r for r in target_vehicles if r["position_m"] >= position]
    behind = [r for r in target_vehicles if r["position_m"] < position]
    front = min(ahead, key=lambda r:r["position_m"]) if ahead else None
    rear = max(behind, key=lambda r:r["position_m"]) if behind else None
    gap_front = front["position_m"] - front["length_m"] - position if front else None
    gap_rear = position - length - rear["position_m"] if rear else None
    required_front = parameters["minimum_gap_m"] + speed * parameters["gap_headway_s"]
    required_rear = parameters["minimum_gap_m"] + (rear["speed_mps"] if rear else 0.) * parameters["gap_headway_s"]
    okay = (gap_front is None or gap_front >= required_front) and (gap_rear is None or gap_rear >= required_rear)
    return {"space_ok": okay, "front_gap_m": gap_front, "rear_gap_m": gap_rear,
        "required_front_gap_m": required_front, "required_rear_gap_m": required_rear,
        "target_lane_vehicles": target_vehicles}


def green_extension_needed(distance, speed, remaining, maximum_extra, parameters=GUARD):
    eta = distance / max(parameters["eta_floor_speed_mps"], speed)
    need = remaining < eta + parameters["green_arrival_buffer_s"] <= remaining + maximum_extra
    return bool(need), eta


def prepare_guarded(seed, policy, run_id):
    if policy not in POLICIES:
        raise ValueError("Unknown guarded emergency policy")
    output, meta = prepare_emergency(seed, "baseline", run_id)
    # Shared scenario and inputs retain identical bytes in all three arms.
    (output / "guard_protocol.json").write_text(json.dumps(GUARD, ensure_ascii=False, indent=2), encoding="utf-8")
    meta.update(emergency_policy=policy, guard_protocol=GUARD,
        guard_protocol_sha256=digest(output / "guard_protocol.json"),
        emergency_generation="guarded_v1")
    (output / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, meta


class GuardedController(EmergencyController):
    def __init__(self, connection, output, metadata):
        super().__init__(connection, output, metadata)
        self.guard = metadata["guard_protocol"]
        self.requested = set()
        self.last_request = {}
        self.pending_responses = []
        self.observed_lane_responses = 0
        self.guard_handle = (output / "guard_decisions.jsonl").open("w", encoding="utf-8")
        self.response_handle = (output / "yield_responses.csv").open("w", encoding="utf-8-sig", newline="")
        self.response_writer = csv.DictWriter(self.response_handle, fieldnames=["requested_at", "observed_at",
            "vehicle_id", "requested_edge", "observed_edge", "requested_lane", "observed_lane", "outcome"])
        self.response_writer.writeheader()

    def observe_responses(self, now):
        remaining = []
        for pending in self.pending_responses:
            if now < pending["requested_at"] + self.guard["response_observation_s"]:
                remaining.append(pending)
                continue
            active = pending["vehicle_id"] in self.c.vehicle.getIDList()
            edge = self.c.vehicle.getRoadID(pending["vehicle_id"]) if active else None
            lane = self.c.vehicle.getLaneIndex(pending["vehicle_id"]) if active else None
            outcome = ("observed_in_requested_lane" if edge == pending["requested_edge"] and lane == pending["requested_lane"]
                else "same_edge_other_lane" if edge == pending["requested_edge"] else "left_edge_or_network_UNDETERMINED")
            self.observed_lane_responses += int(outcome == "observed_in_requested_lane")
            self.response_writer.writerow({**pending, "observed_at":now, "observed_edge":edge, "observed_lane":lane,
                "outcome":outcome})
        self.pending_responses = remaining
        self.response_handle.flush()

    def step(self, now):
        old_action = self.next_action
        # Parent keeps exactly the same incident, trace and safety modes. The
        # new names do not activate its old broad lane-change request branches.
        super().step(now)
        self.observe_responses(now)
        if self.next_action == old_action or self.departed is None or self.arrived is not None:
            return
        details = self.yield_decision(now)
        self.guard_handle.write(json.dumps(details, ensure_ascii=False) + "\n")
        self.guard_handle.flush()
        if self.policy == "guarded_green":
            self.extend_green(now)

    def yield_decision(self, now):
        edge_id = self.c.vehicle.getRoadID(VEHICLE)
        result = {"simulation_seconds":now, "task_edge":edge_id, "policy":self.policy, "candidates":[]}
        if not edge_id or edge_id.startswith(":"):
            return dict(result, action="internal_edge_no_request")
        task_speed = self.c.vehicle.getSpeed(VEHICLE)
        result["task_speed_mps"] = task_speed
        if task_speed > self.guard["maximum_task_speed_mps"]:
            return dict(result, action="task_moving_freely")
        leader = self.c.vehicle.getLeader(VEHICLE, self.guard["lookahead_m"])
        if not leader or not leader[0] or not 0 <= leader[1] < self.guard["lookahead_m"]:
            return dict(result, action="no_close_leader")
        vid, gap = leader
        result.update(leader=vid, leader_gap_m=gap)
        if self.c.vehicle.getRoadID(vid) != edge_id:
            return dict(result, action="leader_on_different_edge")
        position = self.c.vehicle.getLanePosition(vid)
        lane_index = self.c.vehicle.getLaneIndex(vid)
        task_lane = self.c.vehicle.getLaneIndex(VEHICLE)
        speed = self.c.vehicle.getSpeed(vid)
        edge = self.net.getEdge(edge_id)
        if lane_index != task_lane:
            return dict(result, action="leader_on_different_lane")
        result.update(leader_lane=lane_index, leader_position_m=position, leader_speed_mps=speed,
            distance_to_junction_m=edge.getLane(lane_index).getLength() - position)
        if speed > self.guard["maximum_leader_speed_mps"]:
            return dict(result, action="leader_moving_freely")
        if result["distance_to_junction_m"] < self.guard["junction_margin_m"]:
            return dict(result, action="near_junction_no_request")
        if (vid, edge_id) in self.requested or now - self.last_request.get(vid, -1e9) < self.guard["per_vehicle_cooldown_s"]:
            return dict(result, action="repeat_or_cooldown_no_request")
        route = list(self.c.vehicle.getRoute(vid))
        index = self.c.vehicle.getRouteIndex(vid)
        next_edges = route[index + 1:]
        vehicle_class = self.c.vehicle.getVehicleClass(vid)
        length = self.c.vehicle.getLength(vid)
        result.update(leader_length_m=length, leader_class=vehicle_class, leader_next_edges=next_edges)
        for target in [lane_index - 1, lane_index + 1]:
            if not 0 <= target < edge.getLaneNumber():
                continue
            lane = edge.getLane(target)
            candidate = {"lane_index":target, "allowed":lane.allows(vehicle_class)}
            if not candidate["allowed"]:
                result["candidates"].append(candidate)
                continue
            connected, checked = lane_continues(lane, next_edges, vehicle_class,
                lane.getLength() - position, self.guard["route_lookahead_m"])
            candidate.update(route_connected=connected, checked_route_edges=checked)
            neighbors = [{"vehicle_id":other, "position_m":self.c.vehicle.getLanePosition(other),
                "length_m":self.c.vehicle.getLength(other), "speed_mps":self.c.vehicle.getSpeed(other)}
                for other in self.c.lane.getLastStepVehicleIDs(lane.getID()) if other != vid]
            candidate.update(space_check(position, length, speed, neighbors, self.guard))
            candidate["sumo_could_change"] = bool(self.c.vehicle.couldChangeLane(vid, target - lane_index))
            result["candidates"].append(candidate)
        permitted = [r for r in result["candidates"] if r["allowed"] and r["route_connected"] and r["space_ok"] and r["sumo_could_change"]]
        if not permitted:
            return dict(result, action="no_admissible_space_or_route")
        target = permitted[0]["lane_index"]
        result["selected_lane"] = target
        if self.policy == "baseline":
            return dict(result, action="reference_observe_only")
        self.c.vehicle.changeLane(vid, target, self.scenario["yield"]["request_duration_s"])
        self.requested.add((vid, edge_id))
        self.last_request[vid] = now
        self.yield_requests += 1
        self.pending_responses.append({"requested_at":now, "vehicle_id":vid,
            "requested_edge":edge_id, "requested_lane":target})
        self.record(now, "guarded_lane_change_request", vid, {"edge":edge_id, "target_lane_index":target,
            "gap_m":gap, "lane_change_mode":self.c.vehicle.getLaneChangeMode(vid), "decision":result})
        return dict(result, action="requested_once_on_edge")

    def extend_green(self, now):
        for tls, link, distance, state in self.c.vehicle.getNextTLS(VEHICLE)[:1]:
            if distance < 0 or distance > self.scenario["signal"]["lookahead_m_assumed"]:
                continue
            phase = self.c.trafficlight.getPhase(tls)
            if self.seen_phase.get(tls) != phase:
                self.extended.discard(tls)
            self.seen_phase[tls] = phase
            remaining = max(0., self.c.trafficlight.getNextSwitch(tls) - now)
            extra = self.scenario["signal"]["maximum_extension_s_assumed"]
            needed, eta = green_extension_needed(distance, self.c.vehicle.getSpeed(VEHICLE), remaining, extra, self.guard)
            if state in "gG" and needed and tls not in self.extended:
                before = self.c.trafficlight.getRedYellowGreenState(tls)
                self.c.trafficlight.setPhaseDuration(tls, remaining + extra)
                self.extended.add(tls)
                self.green_extensions += 1
                self.record(now, "guarded_existing_green_extension", tls, {"phase":phase, "link_index":link,
                    "state":state, "extra_seconds":extra, "remaining_before_s":remaining,
                    "distance_m":distance, "task_speed_mps":self.c.vehicle.getSpeed(VEHICLE), "eta_s":eta,
                    "state_before":before, "state_after":self.c.trafficlight.getRedYellowGreenState(tls)})

    def state(self, now):
        result = super().state(now)
        result["observed_requested_lane_responses"] = self.observed_lane_responses
        return result

    def close(self):
        for pending in self.pending_responses:
            self.response_writer.writerow({**pending,"observed_at":self.meta["simulation_end_seconds"],
                "observed_edge":None,"observed_lane":None,"outcome":"observation_window_censored_at_cutoff"})
        self.guard_handle.close()
        self.response_handle.close()
        super().close()


def execute_guarded(seed, policy, run_id):
    output, metadata = prepare_guarded(seed, policy, run_id)
    started = time.perf_counter()
    try:
        run_traci(output, metadata, GuardedController)
        metrics = evaluate(output, "peak", metadata)
        metadata.update(status="completed", backend="traci", elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        metadata.update(status="failed", error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise
    finally:
        (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_dir":str(output), "teleports":metrics["teleports"], "collisions":metrics["collisions"]}
