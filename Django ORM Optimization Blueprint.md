# **Helios Technologies: OKA Django ORM Optimization Blueprint**

This blueprint outlines the developer patterns and constraints required to achieve ![][image1] **database-level query complexity**, ![][image2] **spatial lookups**, and ![][image1] **in-memory calculations** using native Django ORM paradigms.

All AI development agents (Antigravity, OpenCode, and Codex) must strictly write code that conforms to these declarative structures. **Raw SQL string queries are strictly forbidden.**

## **1\. N+1 Query Prevention: Relational Fetching**

### **The Rule**

Never access a foreign key relationship or reverse relationship inside a loop, serializer method field, or view-level representation layer without explicitly instructing the database manager to fetch the related entities up-front.

### **The Django ORM Tools**

* select\_related(\*fields): Translates into an SQL INNER JOIN or LEFT OUTER JOIN. Use this for single-value relationships: ForeignKey and OneToOneField (forward or backward).  
* prefetch\_related(\*fields): Translates into a separate SQL query utilizing an IN (...) check and performs the mapping in Python memory. Use this for multi-value relationships: ManyToManyField and reverse foreign key lists.

### **Side-by-Side Implementation Pattern**

#### **❌ The Unoptimized Way (Triggers ![][image3] Database Queries)**

\# viewset.py  
class TripViewSet(viewsets.ModelViewSet):  
    \# This queries only the Trip table.  
    queryset \= Trip.objects.filter(region\_id="REGION\_GH\_KUMASI")  
    serializer\_class \= TripSerializer

\# serializer.py  
class TripSerializer(serializers.ModelSerializer):  
    \# These fields require traversing relationships.  
    driver\_name \= serializers.CharField(source="driver.user.get\_full\_name")  
    pickup\_node\_name \= serializers.CharField(source="pickup\_node.name")  
      
    class Meta:  
        model \= Trip  
        fields \= \['id', 'driver\_name', 'pickup\_node\_name', 'status'\]

\# RESULT FOR 100 TRIPS: 1 query for trips \+ 100 queries for drivers \+ 100 queries for pickup nodes.  
\# TOTAL DATABASE QUERIES: 201 (O(N))

#### **The Optimized Way (Guarantees ![][image1] Database Queries)**

\# viewset.py  
class TripViewSet(viewsets.ModelViewSet):  
    serializer\_class \= TripSerializer

    def get\_queryset(self):  
        \# We enforce a single-pass join on forward relationships.  
        \# select\_related("driver\_\_user") performs an INNER JOIN across Trip, Driver, and User.  
        \# select\_related("pickup\_node") performs an INNER JOIN to the Station Node table.  
        return Trip.objects.for\_region(self.request.region\_id).select\_related(  
            'driver\_\_user',  
            'pickup\_node'  
        )

\# RESULT FOR 10,000 TRIPS: Exactly 1 single, highly indexed database call joining the tables.  
\# TOTAL DATABASE QUERIES: 1 (O(1))

## **2\. N+1 Query Prevention: Annotated Aggregates & Subqueries**

### **The Rule**

Do not use serializers.SerializerMethodField to calculate counts, existences, or averages by executing database queries (e.g., Object.objects.filter(...).count()) for each item in a list serializer. Instead, push the calculation to the database engine using Django annotations or Subquery expressions.

### **Side-by-Side Implementation Pattern**

#### **❌ The Unoptimized Way (Triggers ![][image3] Database Queries)**

\# serializer.py  
class CorridorSerializer(serializers.ModelSerializer):  
    active\_riders\_count \= serializers.SerializerMethodField()

    class Meta:  
        model \= Corridor  
        fields \= \['id', 'name', 'active\_riders\_count'\]

    def get\_active\_riders\_count(self, obj):  
        \# BAD: This executes a "SELECT COUNT(\*)" query for EACH corridor in the list\!  
        return obj.queues.filter(status="WAITING").count()

#### **The Optimized Way (Guarantees ![][image1] Database Queries)**

\# viewset.py  
from django.db.models import Count, Q

class CorridorViewSet(viewsets.ModelViewSet):  
    serializer\_class \= CorridorSerializer

    def get\_queryset(self):  
        \# We pre-calculate the aggregation on the database side using an annotated query.  
        \# This keeps the serialization process purely in memory.  
        return Corridor.objects.for\_region(self.request.region\_id).annotate(  
            annotated\_active\_riders\_count=Count(  
                'queues',  
                filter=Q(queues\_\_status="WAITING")  
            )  
        )

\# serializer.py  
class CorridorSerializer(serializers.ModelSerializer):  
    \# This reads directly from the pre-computed column, executing zero database queries.  
    active\_riders\_count \= serializers.IntegerField(source="annotated\_active\_riders\_count", read\_only=True)

    class Meta:  
        model \= Corridor  
        fields \= \['id', 'name', 'active\_riders\_count'\]

## **3\. Spatial Optimization: ![][image2] Bounding-Box Lookups**

### **The Rule**

All spatial lookups (checking geofences, locating nearest corridors, or calculating sub-stop landmarks) must be executed using **GeoDjango's spatial geometry fields and native spatial database lookups**. This forces the database to query along the GiST (Generalized Search Tree) spatial index, yielding a logarithmic search runtime.

### **Model Setup**

\# models.py  
from django.contrib.gis.db import models as gis\_models

class StationNode(TenantBaseModel):  
    name \= models.CharField(max\_length=100)  
    \# GeoDjango PointField with coordinates (longitude, latitude)  
    coordinates \= gis\_models.PointField(geography=True, spatial\_index=True)

    class Meta:  
        \# Enforce physical spatial indexing at the database engine level  
        indexes \= \[  
            \# Note: GiST creates a balanced bounding-box tree to allow O(log N) geometric searches.  
            \# It spatializes coordinates so we don't scan rows sequentially.  
        \]

### **Side-by-Side Implementation Pattern**

#### **❌ The Unoptimized Way (Triggers ![][image4] Sequential Row Scanning)**

\# service.py  
\# Checking which station node a driver is near without spatial lookups:  
def check\_driver\_proximity\_unoptimized(driver\_lng, driver\_lat):  
    from math import radians, cos, sin, asin, sqrt  
      
    \# BAD: Pulls all database rows into Python memory to run manual math\!  
    \# If there are 1,000 station nodes, it executes 1,000 Haversine calculations.  
    nodes \= StationNode.objects.all()   
    for node in nodes:  
        \# Runs manual O(N) distance checks...  
        pass

#### **The Optimized Way (Guarantees ![][image2] Database Spatial Bounding Lookup)**

\# service.py  
from django.contrib.gis.geos import Point  
from django.contrib.gis.measure import D

def locate\_closest\_station\_node(driver\_longitude: float, driver\_latitude: float, radius\_meters: float \= 150.0):  
    \# Construct a Geospatial Point Object (represented as Longitude, Latitude)  
    driver\_location \= Point(driver\_longitude, driver\_latitude, srid=4326)  
      
    \# We query using the '\_\_dwithin' operator.   
    \# Because of our spatial PointField's GiST index, PostGIS queries the coordinate tree,  
    \# completely bypassing 99.9% of unrelated nodes in under 1 millisecond.  
    nearest\_node \= StationNode.objects.filter(  
        coordinates\_\_dwithin=(driver\_location, D(m=radius\_meters))  
    ).first()  
      
    return nearest\_node

## **4\. In-Memory Computational Optimization: ![][image1] Hash Mapping**

### **The Rule**

When business or dispatch services require matching datasets in application memory (e.g., matching a list of active drivers to a list of waiting passenger groups), never perform nested loop scans (![][image5] complexity) over list collections.

You must convert list collections into hash-mapped dictionaries (dict) or reference sets (set) before executing lookups to enforce true ![][image1] access times.

### **Side-by-Side Implementation Pattern**

#### **❌ The Unoptimized Way (Triggers ![][image5] Exponential Computational Lag)**

\# queue\_dispatcher.py  
def match\_drivers\_to\_manifests\_unoptimized(active\_drivers, ticket\_manifests):  
    \# Both active\_drivers and ticket\_manifests are evaluated list QuerySets.  
    for driver in active\_drivers:  
        \# NESTED LOOP: For each driver, we perform a linear search scan across the ticket list.  
        \# If there are 500 drivers and 500 ticket groups, this runs 500 \* 500 \= 250,000 iterations\!  
        for ticket in ticket\_manifests:  
            if ticket.driver\_id \== driver.id:  
                \# Execution logic...  
                pass

#### **The Optimized Way (Guarantees ![][image1] Inline Lookups, ![][image4] Total Execution Complexity)**

\# queue\_dispatcher.py  
def match\_drivers\_to\_manifests\_optimized(active\_drivers, ticket\_manifests):  
    \# 1\. We construct a Dictionary (Hash Map) of the manifests keyed by driver\_id.  
    \# Creating this dictionary runs in O(T) linear time (where T is the number of tickets).  
    manifests\_by\_driver\_id \= {  
        ticket.driver\_id: ticket   
        for ticket in ticket\_manifests   
        if ticket.driver\_id is not None  
    }  
      
    \# 2\. We iterate over the driver list exactly ONCE.  
    \# Looking up a key in a Python dictionary runs in constant O(1) time.  
    for driver in active\_drivers:  
        \# Instantly retrieve the match without looping over the tickets array.  
        matched\_ticket \= manifests\_by\_driver\_id.get(driver.id)  
        if matched\_ticket:  
            \# Execution logic...  
            pass  
              
    \# TOTAL COMPUTATIONAL COMPLEXITY: O(D \+ T) instead of O(D \* T)  
    \# Execution time for 500 drivers and 500 tickets is reduced from 250,000 loops to exactly 1,000 operations\!

## **5\. Column Optimization: Memory Footprint Reduction**

### **The Rule**

Do not load massive JSON, geometric, metadata, or tracking columns into application memory if they are not used in the immediate execution flow. Utilize .only(\*fields) or .defer(\*fields) to restrict retrieved columns.

### **Side-by-Side Implementation Pattern**

\# viewset.py  
class CorridorListViewSet(viewsets.ReadOnlyModelViewSet):  
    serializer\_class \= MiniCorridorSerializer

    def get\_queryset(self):  
        \# We only need the ID and Name for a drop-down autocomplete menu.  
        \# Using .only() prevents Python from fetching and allocating memory for the massive  
        \# 'GeoJSON\_File' polyline coordinate string stored in the database.  
        return Corridor.objects.for\_region(self.request.region\_id).only('id', 'name')  


[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADIAAAAfCAYAAAClDZ5ZAAACPElEQVR4Xu2Xy0pbURSGNV6qA4sdWBCSnNwgkGkQxUnxgvoAgg5KiwPBeaFIH6EgSOmg7+AjOLGTIo5KOykVHKg4UAcRhCjRtv8Ke8P255yzV2OIIueDRXL+tfa6JDv7nHR1JTxx8vn8HGtxBEEwipcU620FTQXZbPYLin0ulUrP2c/kcrk12DrrPpD/L2ttAc1sSnIM8UauC4VCFtensCuOtRSLxQz8J6xbkHM4quFKpdIP3x/W70PKDPCVHQJ8N1EFZR2aHXA1fIsjyHUoPmuu3wW+b7AN1lvCFDtg3QLftBl0hvRJ2LWrMb5BQI/HrwNJjhWJmt8YbMsVcd3w/TYUgzRj8CHNsq4GTbwyhXbY54IiL0xczdVFS6fTg67GKAf5hV72WFeDBA0pwnucwSCvTUPfrVYul4d8DQrKQT74YmLRFBEw6G8z8JrVcD2lWaupgbzLvphIcGy+1BQRwuJQfIW1MMLWMsg17ouJo3lawOrscMG2WpQ4PppR/K2muGaQTCYz5ouJRVMkKgaDTITpTNR6F+Ra8sXEgsUXcQnsTa1arfaF+JonGeuMcpB1X4wXSYBEP0L0M1iDdRdZK48ZrLtoBoH/Z+CciC2DJOem4C6sboYb5zjGrHnHugC9BjuBHRmT93fuQxZTb571joHt9R5NXLL+n3TLICx2HNNEL+tasH4b9on1joMtsYBG9llXIs9wtyw+GGjmI7bZKus+HtUQFrlBshYHboJF/G95xnpCQkJCQkJCC/wDH/y/VIICIbgAAAAASUVORK5CYII=>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFgAAAAfCAYAAABjyArgAAAD6ElEQVR4Xu2XW0hUURSGRe1GVyMbGJw5zjhgCNGDD0YPRReqt16KgiLpIbCEXrpQPXQhggiCIisLii7UQ5L0UE+R2c0kMLpAREFghXQjEgwry/qXrj2sWe7jDOMoR9gfLI77X2uvvfY6e8455uU5HA5BLBZbqrUgUlpaukZrIw6a5UWj0XrP8+oSicQU7deg6BrYTqmFw+EZ0GZJLQhgX3Hs65nWRwQ05CgW/4ci1tM4Ho9HMf4M+6ljDWVlZRH4O8wYN2c+5WBrkrFBAfs8Bjut9eEknxt7VzsI+P7AerVO0DwUO96mB7XBBNWntWGDm/FW6wb4FvENWKz0ebBfUjOMggZf8kbiUYFFPmRwN/tOOKxBihj36GevIegNxuNvagb7HhpozgJuRLP2SXByizjuu9RJKykpmSA1g1+DEZ+A/hDWYp71Gto8fHvpnYCX7DhcqxDfBTuuYwnknA3fuVAoNJHG5eXlkzFnH83HMF+FJ6EaM3mJZw0W6KFFbM9QCTa7jhv21Gi0CdJknMTWYIxbYW/E+IinXqCo5RDNLS4unoTGTec8l6Evs61HNwD6Tdhajj2Aes+Tj26SbY6BfMi7S+s5gwvyLcCAIl5zMTVGw3jhYHM5d7LBmLvJFs9xD9R4mxg32OYZ4LvN174GY539xpfJIUD8Ba3nBHxezeTN+BZgsMWhsA1ak/CcZINtOVhvkjrHbRbjs7Z5Bvi28/WljsO4VmsS+LphrVrPFQW8mW7tkOBntpLi9CccGlydpnjKfUeNB8Qj73XWC2js9X8Sthk//v4B60pO8IHztyit07amgXMn18o5fpuW+MWgwXNtuoHnpW0wtBapI+9jjNthvTynXcb7QbGYu9yi+T5jOX+j1nOGl+YO43S9I39lZeUYi6/vy0LrBvLJU49xmy3e4xetGA+ISQeauFrPw9qrjMYvyzrpJ7jBu7WeU2gRFPjcon+hzWtdQnMrKirGap3g4lM+5EnDxuvNGP9WzyGNPstEDJ3cb7BHsFuwK6hvhfHbQMwLyqO0G0bD9aP0GbieIq3nHCz0lRbz+j+j6MFPTa/ScRqes1VqKHgJtE+w97AOWKf0Y3yR55HRZ1+h8jcKf4rJOInX/9xO+UamX52Zh71Mkz6CvpkHyxkI0MwdXgYvoExBvlOeuiEGbtYZrWcL/ZKQ75rWAwefgpRTmC3I9QR2X+sE9HacxKtaz5bAn14Dvbk98d/ZUEGuv6qRhdCaYb+FNiRwevcg30GtBxYUexhFb9R6tiBfDHYSjb6H64lIJBLWMdmCF2sIOV9pPfCgGdVaCyI4CFu05nA4HA6Hw+FwOByjgv9rm15++DlcggAAAABJRU5ErkJggg==>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAAAfCAYAAABXscv8AAABkklEQVR4Xu2VvUrEQBSF19VGELFJl59NCDYWNr6BvYWvYeFL2IjvIFtZ2FhYWIh2goLWIoiFgpUrK4KCuOi5OCPDYYMOyUiQ+8ElmXNn7p2cZEinoyiKYsiybBvxjPiQSNO0P2bOyObNnGWeE5perzcnvVlvDPcBOSdAP47juGQ9JGVZRjD79qe9NcEEih8g9kyjVZ5QpznWnrDmS1AD4PJ6kiRLcl/VCNo7a78Fa09Z86VqX42AwgPnfiiN8PnNWq0oinmcwU079gX1zlnzJbQB34XlnJtmV05+J4qiGTv2BesvWPMlpAFy/vddgZvVbdxqA9zz72qm4ZaMcX1z81Xg65mWWhxYf8maDa5RRTADUPSRNcE2hBkLuG5wfhzyr8ZDrXBg/TVrNrhGFSENGFsU+qFpeiNvlvM+ZC0+AlMoesSiodtU07YaMImCD/jEzzhhQf4F8cq6L60zAGd1F8WeEIPs678/4jlCnueLyK2x7ksdA8z+7hF3JuR+yPNaTR0D/gtdFhRFURRFURRFUf6aT2hclMGemYT5AAAAAElFTkSuQmCC>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADQAAAAfCAYAAACoE+4eAAACkUlEQVR4Xu2Wz4tNYRjHxwxCkSlR9173t27NQuou/FgQSjY2CAuRhRprYf4CajZGCf8B5R+QLNhZih1lIWlCzSgMzfjxeabnrdd3jjvn3DvNVc6nnu6c7/d9n+d533Pec2ZgIOc/o1arHVItDdVqdYdqSw7NVcrl8p1KpXKz2WxuUF+hqVFiTPW0UOeXaksCTU1YchZzxq7r9XqZ6/fENx0baDQaW/HfqZ4F5teIKdV7YdAX8kQNA2+O+Km6YfPYiDWqZ4U8X6h/RPWusKaI16oH8A74gg+Kvof4HmvdUiqVtlkN1TNDkrcpEs3fQeJ+LHI928vZUaxGoVBYp3pqaGafN/pYvRjuzLCPm45109jZtbEWwLtLHA3X5DhB3KPmrnhcjNcYVz01tsOWZLEzQCOnvdizoLVarfWmxeMC6D/81+YcI6apsdEW7/VO6RwD/WGll0fYCyY2FUOhl97IaNC43p8015pm3A0fY/nnYt+1R7EW8E/Fgpyp4HW7Oe2CksbR9DnVDL5h2/kZ4neL+XomPNfFWAuwoCtJOdMy5Mln1IihyHEbp690FnS2U3G8W+pzvdu1FbEewLukczLhC+qY4G9j7HAn6QGfNyvaZKc5bNrtTv6iMPlTpwQUeGN+u91eleDNv/lUD5jHmGsJ2tXwd+wZeA/Qv6qeCUvMbj9P0D/oDis2d2RkZLXqnJtN5ukr3RcxaHeXuBB7wUefUD0zJPpoyYinxIwn3qnjFJ+z4IAz96Q3/wdoL0znTlxXz3BvWPVlg+KXaeKz6t1QLBZLSZuw7HgTK1XPCnmmKtF/Fn2Dx+swjbxSPQv+zZpUvW/QzDiP33nV0/JPPGqKfWhVSwN3Z69qOTk5OTk5OX3mNxeFxz1lf78UAAAAAElFTkSuQmCC>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAD4AAAAfCAYAAAC/MX7XAAAC90lEQVR4Xu2XuYtUQRDG91DxwGNBHBhm5s0FAxOYjOKqgaggxioqIl4grImJILrBZh6Y6IKIBgZqoCD6FxhoICyI4JF4gIGIiG6goKyy6/HVbrXWfL6d6Xm+ZQZ8Pyjm9Vfd1VXd/Y7p6kpoP0EQPID9hN1nX8dTKBQ2seYDih0111dh49afz+d32vaMgiKCXC53EUmcL5fLi9jPILkB2DHWfZCdxtjT2uyWtvUjjyK0x1aLHSRwTibGZHukXSwWc2i/h33lvo5SqZSF/y3rUchmsyu5cAF5DcMusR4HPVrwPXYI8E3AfrAu6I7NZT0Kcsxhu1gXwhbkn5GgsFesO+DboAuzkfQ1sG9WiwriXG50P8N/LYjzyCPYG4/VnDwRsJtWRHs86r1tQZwDiNMv11jctewXcNst9sjTD0y2Tgu6yz4LkunTfh+tLlomk5lnNQd812FbXBsxtsNuuAIduK9XyMMNtg/+gxjz3PotMp/Pw7YpsmMSrNk9ioR2a+GPnFapVBaKZvs5oH/XXxmzVRYMcyyRRdL5fh9p7VNnfyLVo2OPs94yzSZyYLIXOumA09BeHzZWikO/Ye0j8SesX7U7VvNFc7jCekvgNbTMt/CwfkhgP2sCvgGW46cXvynxp9Pp+davsY5YzReMG4ONsN4qvZrEGDssOObbpB+/6lD43rDCHfBdYD/aq1XrtrovGPsF9pD1ltHCp01emK4PCu8P0x06ru7zE+13jcY0Q2PeZr1lEORTo0Swy6/FX6vVZof4Jp/0rDvEhz6nQrST7tr6fNDCB1mPhATD7j0J0T8EtGOMjK1Wq3NYx329VHz8qtNie+S0wA5Znw+6cH2sRwYBR3U1R4KpB4gsxirux+iYvx5UGLtDi6wD2lNN/iz7mpFKpRaExWwLKOAokvnM+kyQm/q3eIv1tqG7MIv1uOmY3XbgWG9GUi9ZjxPs9hDmOMF620FSZ+Q7m/U40A+hZ6x3DPJBw1ocYEEPs5aQkJCQkJCQ8F/xCyzI5G58Wt7qAAAAAElFTkSuQmCC>