"""Sparse yielding must preserve usable routes, space and fixed exogenous input."""
# AI-assisted: OpenAI Codex; actual usage and human review retained for submission.
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from b_twin.emergency_guard import GUARD,GuardedController,prepare_guarded,lane_continues,space_check,green_extension_needed
from b_twin.emergency import VEHICLE


class GuardedContracts(unittest.TestCase):
    def test_front_and_rear_gaps_use_vehicle_lengths_and_rear_speed(self):
        self.assertTrue(space_check(50,5,2,[])['space_ok'])
        front=[dict(vehicle_id='f',position_m=56,length_m=5,speed_mps=0)]
        self.assertFalse(space_check(50,5,2,front)['space_ok'])
        rear=[dict(vehicle_id='r',position_m=40,length_m=5,speed_mps=10)]
        self.assertFalse(space_check(50,5,2,rear)['space_ok'])
        rear[0]['position_m']=20
        self.assertTrue(space_check(50,5,2,rear)['space_ok'])

    def test_route_check_includes_next_turn_and_rejects_later_dead_end(self):
        class Edge:
            def __init__(self,name):self.name=name
            def getID(self):return self.name
        class Lane:
            def __init__(self,name):self.edge=Edge(name);self.out=[]
            def getEdge(self):return self.edge
            def getOutgoing(self):return self.out
            def allows(self,c):return True
            def getLength(self):return 30
        class Connection:
            def __init__(self,to):self.to=to
            def getToLane(self):return self.to
        a,b,c=Lane('a'),Lane('b'),Lane('c')
        a.out=[Connection(b)]
        good,checked=lane_continues(a,['b','c'],'passenger',10,120)
        self.assertFalse(good)
        self.assertEqual(checked,['b','c'])
        b.out=[Connection(c)]
        self.assertTrue(lane_continues(a,['b','c'],'passenger',10,120)[0])
        self.assertFalse(lane_continues(a,['c'],'passenger',500,120)[0])

    def test_only_one_request_per_vehicle_edge_and_baseline_observes_only(self):
        obj=GuardedController.__new__(GuardedController)
        obj.c=Mock();obj.net=Mock();obj.guard=GUARD;obj.policy='guarded'
        obj.requested=set();obj.last_request={};obj.pending_responses=[];obj.yield_requests=0
        obj.scenario={'yield':{'request_duration_s':3.}}
        obj.record=Mock()
        obj.c.vehicle.getRoadID.return_value='edge'
        obj.c.vehicle.getSpeed.return_value=1.
        obj.c.vehicle.getLeader.return_value=('front',12.)
        obj.c.vehicle.getLanePosition.return_value=50.
        obj.c.vehicle.getLaneIndex.return_value=1
        obj.c.vehicle.getVehicleClass.return_value='passenger'
        obj.c.vehicle.getLength.return_value=5.
        obj.c.vehicle.getRoute.return_value=['edge','next']
        obj.c.vehicle.getRouteIndex.return_value=0
        obj.c.vehicle.couldChangeLane.return_value=True
        obj.c.vehicle.getLaneChangeMode.return_value=1621
        obj.c.lane.getLastStepVehicleIDs.return_value=[]
        obj.net.getEdge.return_value.getLaneNumber.return_value=3
        obj.net.getEdge.return_value.getLane.return_value.getLength.return_value=200
        obj.net.getEdge.return_value.getLane.return_value.allows.return_value=True
        with patch('b_twin.emergency_guard.lane_continues',return_value=(True,['next'])):
            self.assertEqual(obj.yield_decision(2500)['action'],'requested_once_on_edge')
            self.assertEqual(obj.yield_decision(2502)['action'],'repeat_or_cooldown_no_request')
            self.assertEqual(obj.yield_decision(2600)['action'],'repeat_or_cooldown_no_request')
            self.assertEqual(obj.c.vehicle.changeLane.call_count,1)
            obj.policy='baseline';obj.requested.clear();obj.last_request.clear()
            self.assertEqual(obj.yield_decision(2700)['action'],'reference_observe_only')
            self.assertEqual(obj.c.vehicle.changeLane.call_count,1)
        obj.c.vehicle.setLaneChangeMode.assert_not_called()
        obj.c.vehicle.setSpeedMode.assert_not_called()
        obj.c.vehicle.moveTo.assert_not_called()

    def test_green_extension_requires_reachable_end_of_current_green(self):
        self.assertTrue(green_extension_needed(30,3,5,10)[0])
        self.assertFalse(green_extension_needed(30,3,20,10)[0])
        self.assertFalse(green_extension_needed(120,0,1,10)[0])

    def test_new_arms_keep_all_task_event_and_ordinary_inputs_identical(self):
        with tempfile.TemporaryDirectory() as temp,patch('b_twin.runner.APP',Path(temp)):
            runs=[prepare_guarded(20261023,p,p)[0] for p in ['baseline','guarded','guarded_green']]
            for name in ['network.net.xml','demand.rou.xml','vehicles.csv','emergency_scenario.json','meter_plan.json',
                    'boundary_supply.json','guard_protocol.json']:
                self.assertTrue(all((p/name).read_bytes()==(runs[0]/name).read_bytes() for p in runs),name)
            self.assertEqual(json.loads((runs[0]/'guard_protocol.json').read_text())['version'],'guarded_yield_v1')


if __name__=='__main__':unittest.main()
