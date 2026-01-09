# Simulateur d'Admission de Flux Basé sur les Événements

Ce projet implémente un simulateur d'événements pour le contrôle d'admission de flux vers plusieurs serveurs dans un environnement d'informatique en nuage (edge computing). Le simulateur est conçu pour tester différentes politiques d'admission et évaluer les performances du système.

## Vue d'ensemble

Le simulateur modélise un système où :
- **M classes de flux** arrivent selon des processus de Poisson
- **S serveurs** hébergent des applications avec capacités limitées (calcul et accès)
- **Un équilibreur de charge** route les flux vers les serveurs
- **Une politique d'admission** décide d'accepter ou rejeter les flux en fonction des contraintes et de l'utilité

## Architecture du Système

### Entités Principales

#### 1. **Flow (Flux)**
Représente un flux de données avec :
- `fid` : identifiant unique
- `j` : classe du flux (0 à M-1)
- `bitrate` : débit binaire consommé
- `t_arrival` : temps d'arrivée
- `duration` : durée du flux

#### 2. **Server (Serveur)**
Héberge les flux actifs avec :
- `psi` (ψ) : capacité de calcul (nombre maximum de flux actifs)
- `theta` (θ) : capacité d'accès (bande passante totale disponible)
- `Xij` : nombre de flux actifs de chaque classe j
- `Yi` : charge de calcul totale (nombre de flux actifs)
- `Bi` : charge d'accès totale (bande passante utilisée)
- `apps` : liste des applications hébergées

#### 3. **Application**
Représente une application avec :
- `name` : nom unique
- `chi` : vecteur d'affinité (chi[j]=1 si classe j est supportée)
- `utility` : fonction d'utilité f(j, w_jd)

#### 4. **RandomizedLoadBalancer**
Distribue les flux aux serveurs via :
- `u[i][j]` : probabilité de router un flux classe j vers le serveur i
- Normalisation automatique et CDF pour le routage efficace

#### 5. **AdmissionPolicy**
Interface pour les politiques d'admission (pluggable) :
- Méthode `decide()` prenant en compte les contraintes et l'utilité

### Politiques d'Admission Disponibles

#### **HeuristicAdmission**
Politique basée sur des heuristiques simples :
```python
net_gain = reward - congestion >= min_net_gain
```

Paramètres :
- `penalty_access` : pénalité pour la saturation d'accès
- `penalty_compute` : pénalité pour la saturation de calcul
- `min_net_gain` : gain net minimum requis pour accepter

## Composants Logiques

### Composant 1 : Générateur de Trafic de Zone
```python
_schedule_next_arrival(t_now, j)
_new_flow(t, j)
```
- Génère les inter-arrivées selon une loi exponentielle (paramètre `zeta[j]`)
- Crée les flux avec durée exponentielle et débit configurable

### Composant 2 : Équilibreur de Charge
```python
lb.route(rng, j)
```
- Sélectionne aléatoirement un serveur selon `u[i][j]`
- Support de distributions de probabilité générales

### Composant 3 : Serveur
```python
server.can_host_compute()
server.can_host_access(bitrate)
server.admit(flow, t_depart)
server.depart(fid)
```
- Vérifie les contraintes de capacité
- Gère l'admission et le départ des flux
- Maintient l'état des charges

### Composant 4 : Contrôle d'Admission
```python
policy.decide(t, server, flow, global_wjd)
```
- Prend la décision d'admission basée sur :
  - Contraintes matérielles (saturation)
  - Utilité de l'application
  - Congestion du serveur

### Composant 5 : Application
```python
app = Application(name="A", chi=[1, 0, 1], utility=diminishing_utility(...))
```
- Fonction d'utilité paramétrée : f(j, w_jd)
- Affinité avec les classes de flux

## Utilisation

### Installation
```bash
pip install matplotlib
```

### Exemple Basique

```python
# 1. Créer les serveurs avec applications
appA = Application(
    name="A",
    chi=[1, 1, 0],
    utility=diminishing_utility(base=1.2, alpha=0.20)
)
servers = [
    Server(i=0, psi=6, theta=6.5, apps=[appA]),
    Server(i=1, psi=5, theta=5.0, apps=[appA]),
]

# 2. Configurer l'équilibreur de charge
u = [[0.5, 0.5], [0.5, 0.5]]  # probabilités de routage
lb = RandomizedLoadBalancer(u=u)

# 3. Choisir la politique d'admission
policy = HeuristicAdmission(penalty_access=1.1, penalty_compute=0.8)

# 4. Créer et lancer le simulateur
sim = EventDrivenSimulator(
    M=2,  # 2 classes
    servers=servers,
    zeta=[0.6, 0.45],  # taux d'arrivée
    duration_samplers=[make_exp_sampler(0.35), make_exp_sampler(0.25)],
    bitrate_samplers=[constant(1.2), constant(1.0)],
    load_balancer=lb,
    admission_policy=policy,
    seed=42
)

# 5. Exécuter et visualiser
sim.run(t_end=100.0)
sim.plot(title="Contrôle d'admission")
```

### Démonstration Complète
```bash
python script.py
```

Lance une démonstration avec 3 classes, 3 serveurs et 2 applications.

## Paramètres Clés

| Paramètre | Type | Description |
|-----------|------|-------------|
| `M` | int | Nombre de classes de flux |
| `zeta[j]` | float | Taux d'arrivée pour classe j |
| `mu[j]` | float | Taux de départ (1/durée moyenne) pour classe j |
| `psi` | int | Capacité de calcul du serveur |
| `theta` | float | Capacité d'accès du serveur |
| `u[i][j]` | float | Probabilité de routage classe j → serveur i |

## Visualisations

Le simulateur génère 2 graphiques par serveur :

### Haut : Charge de Calcul Y_i(t)
- Courbe en escalier montrant le nombre de flux actifs
- Ligne pointillée : seuil de saturation (psi)
- Marqueurs colorés : arrivées de flux par classe
  - **Couleur** : classe du flux
  - **Forme** : raison de la décision d'admission

### Bas : Charge d'Accès B_i(t)
- Courbe en escalier montrant la bande passante utilisée
- Ligne pointillée : seuil de saturation (theta)
- Marqueurs pour rejets par saturation d'accès

### Légende des Marqueurs

| Marqueur | Signification |
|----------|---------------|
| `o` | Accepté |
| `X` | Rejeté : calcul saturé |
| `s` | Rejeté : accès saturé |
| `x` | Rejeté : politique |
| `--` | Seuil de capacité |

## Fonctions Utilitaires

### `expovariate_rate(rng, rate)`
Génère une variable aléatoire exponentielle avec taux de rate.

### `make_exp_sampler(rate)`
Retourne une fonction de sampling exponentiel.

### `constant(value)`
Retourne une fonction retournant une constante.

### `diminishing_utility(base, alpha)`
Utilité décroissante avec congestion :
```
u(j, w) = base / (1 + alpha * w)
```

## Structure des Données de Résultat

```python
{
    "times_Y": [[t1, t2, ...], ...],  # temps pour Y_i(t)
    "values_Y": [[y1, y2, ...], ...], # valeurs de Y_i(t)
    "times_B": [[t1, t2, ...], ...],  # temps pour B_i(t)
    "values_B": [[b1, b2, ...], ...], # valeurs de B_i(t)
    "arrival_points": [
        [(t, Y_before, B_before, accepted, j, reason), ...],
        ...
    ]
}
```

## Personnalisation

### Ajouter une Nouvelle Politique d'Admission
```python
class MyAdmissionPolicy(AdmissionPolicy):
    def decide(self, *, t, server, flow, global_wjd):
        # Votre logique ici
        return True  # ou False
```

### Utiliser des Distributions Personnalisées
```python
def my_duration_sampler(rng):
    return rng.uniform(5, 15)

duration_samplers = [my_duration_sampler, ...]
```

### Modifier les Applications
```python
apps = [
    Application(
        name="CustomApp",
        chi=[1, 0, 1, 0],
        utility=lambda j, w: 2.0 / (1.0 + 0.5 * w)
    )
]
```

## Notes Importantes

1. **Compatibilité avec l'article de recherche** : Le code utilise les notations du papier [1] :
   - M : zones/classes
   - S : nombre de serveurs
   - ψ (psi) : capacité de calcul
   - θ (theta) : capacité d'accès
   - w_{j,d} : charge de classe j sur serveur d

2. **Reproduction des résultats** : Utilisez le paramètre `seed` pour la reproductibilité.

3. **Extensibilité** : Le simulateur est conçu pour intégrer facilement :
   - De nouvelles politiques (RL, optimales, etc.)
   - Des fonctions d'utilité complexes
   - Des stratégies de routage avancées

4. **Performance** : Pour des simulations longues (t > 1000), augmentez le seed et validez les résultats statistiques.

## Fichiers

- `admission_control.py` : Code principal du simulateur
- `README.md` : Ce fichier

## Références

[1] A. Fox, F. De Pellegrini, F. Faticanti, E. Altman, F. Bronzino
*"Optimal flow admission control in edge computing via safe reinforcement learning"*
IEEE WiOPT 2024, Séoul, Corée du Sud, Octobre 2024

## Auteurs et Instructeurs

- CLEQUE–MARLAIN MBOULOU–MOUTOUBI
- FRANCESCO DE PELLEGRINI

## Licence

À définir selon vos besoins institutionnels.