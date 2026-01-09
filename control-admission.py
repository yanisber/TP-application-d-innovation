import heapq
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

import matplotlib.pyplot as plt


def expovariate_rate(rng: random.Random, rate: float) -> float:
    if rate <= 0:
        raise ValueError("rate must be > 0")
    return rng.expovariate(rate)

def constant(value: float) -> Callable[[random.Random], float]:
    return lambda rng: value

def make_exp_sampler(rate: float) -> Callable[[random.Random], float]:
    return lambda rng: expovariate_rate(rng, rate)


# ---------------------------
# Core entities
# ---------------------------

@dataclass(frozen=True)
class Flow:
    fid: int
    j: int
    bitrate: float
    t_arrival: float
    duration: float


@dataclass
class Application:
    name: str
    chi: List[int]  # chi[j] in {0,1}
    utility: Callable[[int, int], float]  # (j, w_jd) -> reward


@dataclass
class Server:
    i: int
    psi: int
    theta: float
    apps: List[Application]

    Xij: List[int] = field(default_factory=list)
    Yi: int = 0
    Bi: float = 0.0
    active: Dict[int, Tuple[Flow, float]] = field(default_factory=dict)

    def init_state(self, M: int):
        self.Xij = [0] * M
        self.Yi = 0
        self.Bi = 0.0
        self.active = {}

    def can_host_compute(self) -> bool:
        return self.Yi < self.psi

    def can_host_access(self, add_bitrate: float) -> bool:
        return (self.Bi + add_bitrate) <= self.theta

    def admit(self, flow: Flow, t_depart: float):
        self.active[flow.fid] = (flow, t_depart)
        self.Xij[flow.j] += 1
        self.Yi += 1
        self.Bi += flow.bitrate

    def depart(self, fid: int) -> Flow:
        flow, _tdep = self.active.pop(fid)
        self.Xij[flow.j] -= 1
        self.Yi -= 1
        self.Bi -= flow.bitrate
        return flow


# ---------------------------
# Admission policy (plug-in)
# ---------------------------

class AdmissionPolicy:
    def decide(self, *,
               t: float,
               server: Server,
               flow: Flow,
               global_wjd: Dict[Tuple[str, int], int]) -> bool:
        raise NotImplementedError


class HeuristicAdmission(AdmissionPolicy):
    def __init__(self, penalty_access: float = 1.0, penalty_compute: float = 0.5, min_net_gain: float = 0.0):
        self.penalty_access = penalty_access
        self.penalty_compute = penalty_compute
        self.min_net_gain = min_net_gain

    def decide(self, *, t: float, server: Server, flow: Flow, global_wjd: Dict[Tuple[str, int], int]) -> bool:
        # hard constraints (the simulator will also tag reasons)
        if not server.can_host_compute():
            return False
        if not server.can_host_access(flow.bitrate):
            return False

        reward = 0.0
        for app in server.apps:
            if app.chi[flow.j] == 1:
                w_jd = global_wjd[(app.name, flow.j)]
                reward += app.utility(flow.j, w_jd)

        congestion = (
            self.penalty_compute * (server.Yi / max(1, server.psi)) +
            self.penalty_access * (server.Bi / max(1e-9, server.theta))
        )
        return (reward - congestion) >= self.min_net_gain


# ---------------------------
# Load balancer
# ---------------------------

class RandomizedLoadBalancer:
    def __init__(self, u: List[List[float]]):
        self.u = u
        self.M = len(u)
        self.C = len(u[0]) if u else 0

        self.cdf_per_class: List[List[float]] = []
        for j in range(self.C):
            probs = [self.u[i][j] for i in range(self.M)]
            s = sum(probs)
            if s <= 0:
                raise ValueError(f"Routing probs for class {j} sum to 0.")
            probs = [p / s for p in probs]
            cdf, acc = [], 0.0
            for p in probs:
                acc += p
                cdf.append(acc)
            cdf[-1] = 1.0
            self.cdf_per_class.append(cdf)

    def route(self, rng: random.Random, j: int) -> int:
        x = rng.random()
        cdf = self.cdf_per_class[j]
        for i, c in enumerate(cdf):
            if x <= c:
                return i
        return len(cdf) - 1


# ---------------------------
# Event-driven simulator
# ---------------------------

@dataclass(order=True)
class Event:
    t: float
    kind: str = field(compare=False)  # "ARRIVAL" or "DEPART"
    j: int = field(compare=False, default=-1)
    fid: int = field(compare=False, default=-1)
    server_i: int = field(compare=False, default=-1)


class EventDrivenSimulator:
    def __init__(self,
                 *,
                 M: int,
                 servers: List[Server],
                 zeta: List[float],
                 duration_samplers: List[Callable[[random.Random], float]],
                 bitrate_samplers: List[Callable[[random.Random], float]],
                 load_balancer: RandomizedLoadBalancer,
                 admission_policy: AdmissionPolicy,
                 seed: int = 0):
        self.M = M
        self.servers = servers
        self.S = len(servers)
        self.zeta = zeta
        self.duration_samplers = duration_samplers
        self.bitrate_samplers = bitrate_samplers
        self.lb = load_balancer
        self.policy = admission_policy
        self.rng = random.Random(seed)

        for srv in self.servers:
            srv.init_state(M)

        # global w_{j,d}
        self.global_wjd: Dict[Tuple[str, int], int] = {}
        app_names = set()
        for srv in self.servers:
            for app in srv.apps:
                app_names.add(app.name)
        for name in app_names:
            for j in range(M):
                self.global_wjd[(name, j)] = 0

        self.pq: List[Event] = []
        self.next_fid = 0

        # --- For plotting (staircases + markers) ---
        # compute load Y_i(t)
        self.times_Y: List[List[float]] = [[] for _ in range(self.S)]
        self.values_Y: List[List[int]] = [[] for _ in range(self.S)]
        # access load B_i(t)
        self.times_B: List[List[float]] = [[] for _ in range(self.S)]
        self.values_B: List[List[float]] = [[] for _ in range(self.S)]

        # arrival markers with reasons
        # tuple: (t, Y_before, B_before, accepted, class_j, reason)
        # reason in {"ACCEPT", "SAT_COMPUTE", "SAT_ACCESS", "POLICY"}
        self.arrival_points: List[List[Tuple[float, int, float, bool, int, str]]] = [[] for _ in range(self.S)]

    def _schedule_next_arrival(self, t_now: float, j: int):
        inter = expovariate_rate(self.rng, self.zeta[j])
        heapq.heappush(self.pq, Event(t=t_now + inter, kind="ARRIVAL", j=j))

    def _new_flow(self, t: float, j: int) -> Flow:
        fid = self.next_fid
        self.next_fid += 1
        b = self.bitrate_samplers[j](self.rng)
        dur = self.duration_samplers[j](self.rng)
        return Flow(fid=fid, j=j, bitrate=b, t_arrival=t, duration=dur)

    def _record_state(self, server_i: int, t: float):
        srv = self.servers[server_i]
        self.times_Y[server_i].append(t)
        self.values_Y[server_i].append(srv.Yi)
        self.times_B[server_i].append(t)
        self.values_B[server_i].append(srv.Bi)

    def run(self, *, t_end: float):
        # init arrivals
        for j in range(self.M):
            self._schedule_next_arrival(0.0, j)

        # initial state at t=0
        for i in range(self.S):
            self._record_state(i, 0.0)

        while self.pq:
            ev = heapq.heappop(self.pq)
            t = ev.t
            if t > t_end:
                break

            if ev.kind == "ARRIVAL":
                # schedule next arrival for class j
                self._schedule_next_arrival(t, ev.j)

                flow = self._new_flow(t, ev.j)
                i = self.lb.route(self.rng, flow.j)
                srv = self.servers[i]

                Y_before = srv.Yi
                B_before = srv.Bi

                # Tag refusal reasons *before* policy:
                if not srv.can_host_compute():
                    accepted = False
                    reason = "SAT_COMPUTE"
                elif not srv.can_host_access(flow.bitrate):
                    accepted = False
                    reason = "SAT_ACCESS"
                else:
                    # constraints ok → let policy decide
                    accepted = self.policy.decide(t=t, server=srv, flow=flow, global_wjd=self.global_wjd)
                    reason = "ACCEPT" if accepted else "POLICY"

                self.arrival_points[i].append((t, Y_before, B_before, accepted, flow.j, reason))

                if accepted:
                    t_dep = t + flow.duration
                    srv.admit(flow, t_dep)

                    # update global w_{j,d} for apps installed on server
                    for app in srv.apps:
                        if app.chi[flow.j] == 1:
                            self.global_wjd[(app.name, flow.j)] += 1

                    # schedule departure
                    heapq.heappush(self.pq, Event(t=t_dep, kind="DEPART", fid=flow.fid, server_i=i))

                    # record updated state
                    self._record_state(i, t)

            elif ev.kind == "DEPART":
                i = ev.server_i
                srv = self.servers[i]
                if ev.fid not in srv.active:
                    continue

                flow = srv.depart(ev.fid)

                for app in srv.apps:
                    if app.chi[flow.j] == 1:
                        self.global_wjd[(app.name, flow.j)] -= 1

                self._record_state(i, t)

        return {
            "times_Y": self.times_Y,
            "values_Y": self.values_Y,
            "times_B": self.times_B,
            "values_B": self.values_B,
            "arrival_points": self.arrival_points,
        }

    def plot(self, *, title: str = "Admission control – per-server"):
        # consistent color per class
        present_classes = set()
        for i in range(self.S):
            for (_t, _y, _b, _acc, j, _r) in self.arrival_points[i]:
                present_classes.add(j)
        present_classes = sorted(present_classes)

        prop_cycle = plt.rcParams['axes.prop_cycle'].by_key()['color']
        class_color = {j: prop_cycle[idx % len(prop_cycle)] for idx, j in enumerate(present_classes)}

        # marker per rejection reason
        reason_marker = {
            "ACCEPT": "o",
            "SAT_COMPUTE": "X",  # strong marker: compute saturation
            "SAT_ACCESS": "s",   # square: access saturation
            "POLICY": "x",       # x: rejected by policy (not saturated)
        }

        for i in range(self.S):
            srv = self.servers[i]

            fig = plt.figure()
            gs = fig.add_gridspec(2, 1, height_ratios=[2.2, 1.6], hspace=0.25)
            axY = fig.add_subplot(gs[0, 0])
            axB = fig.add_subplot(gs[1, 0], sharex=axY)

            # --- Top: compute load staircase Y_i(t) ---
            axY.step(self.times_Y[i], self.values_Y[i], where="post")
            axY.axhline(srv.psi, linestyle="--")  # psi threshold
            axY.set_ylabel(f"Y_{i+1}(t)  (psi={srv.psi})")
            axY.set_title(f"{title} – Server {i+1} (theta={srv.theta})")
            axY.grid(True, alpha=0.25)

            # markers on Y plot at arrival instants (use Y_before)
            for (t, Yb, Bb, accepted, j, reason) in self.arrival_points[i]:
                m = reason_marker[reason]
                axY.scatter([t], [Yb], marker=m, s=55, color=class_color[j])

            # --- Bottom: access load staircase B_i(t) ---
            axB.step(self.times_B[i], self.values_B[i], where="post")
            axB.axhline(srv.theta, linestyle="--")  # theta threshold
            axB.set_xlabel("time")
            axB.set_ylabel(f"B_{i+1}(t)  (theta={srv.theta})")
            axB.grid(True, alpha=0.25)

            # Option: show rejected-by-access points on B plot too
            for (t, Yb, Bb, accepted, j, reason) in self.arrival_points[i]:
                if reason == "SAT_ACCESS":
                    axB.scatter([t], [Bb], marker=reason_marker[reason], s=55, color=class_color[j])

            # --- Legend ---
            handles, labels = [], []
            for j in present_classes:
                handles.append(plt.Line2D([0], [0], marker="o", linestyle="", color=class_color[j], markersize=7))
                labels.append(f"class j={j+1}")

            handles += [
                plt.Line2D([0], [0], marker=reason_marker["ACCEPT"], linestyle="", color="black", markersize=7),
                plt.Line2D([0], [0], marker=reason_marker["SAT_COMPUTE"], linestyle="", color="black", markersize=7),
                plt.Line2D([0], [0], marker=reason_marker["SAT_ACCESS"], linestyle="", color="black", markersize=7),
                plt.Line2D([0], [0], marker=reason_marker["POLICY"], linestyle="", color="black", markersize=7),
                plt.Line2D([0], [0], linestyle="--", color="black"),
            ]
            labels += [
                "ACCEPT (o)",
                "REJECT: compute saturated (X)",
                "REJECT: access saturated (s)",
                "REJECT: policy (x)",
                "capacity threshold (--)",
            ]
            axY.legend(handles, labels, loc="best")

        plt.show()


# ---------------------------
# Demo scenario (3 classes, 3 servers)
# ---------------------------

def diminishing_utility(base: float, alpha: float) -> Callable[[int, int], float]:
    def u(_j: int, w: int) -> float:
        return base / (1.0 + alpha * w)
    return u

def build_demo():
    M = 3
    zeta = [0.6, 0.45, 0.35]
    mu = [0.35, 0.25, 0.20]

    duration_samplers = [make_exp_sampler(rate=mu[j]) for j in range(M)]
    bitrate_samplers = [constant(1.2), constant(1.0), constant(0.8)]

    appA = Application(name="A", chi=[1, 1, 0], utility=diminishing_utility(base=1.2, alpha=0.20))
    appB = Application(name="B", chi=[0, 1, 1], utility=diminishing_utility(base=1.0, alpha=0.15))

    servers = [
        Server(i=0, psi=6, theta=6.5, apps=[appA, appB]),
        Server(i=1, psi=5, theta=5.0, apps=[appA]),
        Server(i=2, psi=4, theta=4.0, apps=[appB]),
    ]

    u = [
        [0.45, 0.25, 0.15],
        [0.40, 0.50, 0.20],
        [0.15, 0.25, 0.65],
    ]
    lb = RandomizedLoadBalancer(u=u)

    policy = HeuristicAdmission(penalty_access=1.1, penalty_compute=0.8, min_net_gain=0.15)

    sim = EventDrivenSimulator(
        M=M,
        servers=servers,
        zeta=zeta,
        duration_samplers=duration_samplers,
        bitrate_samplers=bitrate_samplers,
        load_balancer=lb,
        admission_policy=policy,
        seed=7,
    )
    return sim

def main():
    sim = build_demo()
    sim.run(t_end=80.0)
    sim.plot(title="Event-driven admission control (demo)")

if __name__ == "__main__":
    main()
