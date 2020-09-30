"""Source for architecture.png, the architecture diagram."""

import os

from diagrams import Cluster, Diagram
from diagrams.gcp.compute import KubernetesEngine
from diagrams.gcp.database import Datastore, SQL
from diagrams.gcp.network import LoadBalancing
from diagrams.gcp.storage import PersistentDisk
from diagrams.onprem.client import User
from diagrams.onprem.compute import Server
from diagrams.programming.framework import React

os.chdir(os.path.dirname(__file__))

graph_attr = {
    "label": "",
    "pad": "0.2",
}

node_attr = {
    "fontsize": "10.0",
}

with Diagram(
    "Token management",
    show=False,
    filename="architecture",
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    user = User("End User")
    frontend = React("Web UI")

    with Cluster("Kubernetes"):
        ingress = LoadBalancing("NGINX Ingress")
        kafka = KubernetesEngine("Kafka")

        with Cluster("Gafaelfawr"):
            server = KubernetesEngine("Server")
            postgresql = SQL("PostgreSQL")
            redis = Datastore("Redis")
            redis_storage = PersistentDisk("Redis Storage")

            user >> frontend >> ingress >> server >> redis >> redis_storage
            server >> postgresql

            kafka_listener = KubernetesEngine("Kafka Listener")

            server >> kafka >> kafka_listener >> postgresql

            housekeeping = KubernetesEngine("Housekeeping")

            postgresql << housekeeping
            redis << housekeeping

        app = KubernetesEngine("Application")

        ingress >> app

    idp = Server("Identity Provider")

    server >> idp
    user >> idp
