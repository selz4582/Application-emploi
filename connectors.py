"""Connecteurs officiels, jamais de scraping implicite."""
import json
import re
import urllib.parse
import urllib.request
import urllib.error
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class FranceTravailConnector:
    """Recherche explicite dans l'API officielle France Travail, avec identifiants OAuth."""
    TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
    SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    SCOPE = "api_offresdemploiv2 o2dsoffre"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()

    def _token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("Identifiants API France Travail absents : connecteur non activé")
        body = urllib.parse.urlencode({"grant_type":"client_credentials","client_id":self.client_id,"client_secret":self.client_secret,"scope":self.SCOPE}).encode()
        request = urllib.request.Request(self.TOKEN_URL, data=body, headers={"Content-Type":"application/x-www-form-urlencoded","User-Agent":"CarnetEmploi42/1.0"})
        token = _request_json(request, "France Travail").get("access_token", "")
        if not token: raise RuntimeError("L'API France Travail n'a pas fourni de jeton d'accès")
        return token

    def search_loire(self, keyword="", city="", limit=20):
        limit = max(1, min(int(limit), 50))
        params = {"departement":"42", "range":f"0-{limit-1}"}
        if keyword.strip(): params["motsCles"] = keyword.strip()
        if city.strip(): params["commune"] = city.strip()
        request = urllib.request.Request(self.SEARCH_URL+"?"+urllib.parse.urlencode(params), headers={"Authorization":f"Bearer {self._token()}","Accept":"application/json","User-Agent":"CarnetEmploi42/1.0"})
        payload = _request_json(request, "France Travail")
        return [self.normalize(item) for item in payload.get("resultats", [])]

    @staticmethod
    def normalize(item):
        company=item.get("entreprise") or {}; location=item.get("lieuTravail") or {}; origin=item.get("origineOffre") or {}
        return {"reference":item.get("id", ""),"source":"France Travail","title":item.get("intitule", ""),"company":company.get("nom") or "Entreprise non renseignée","city":location.get("libelle", ""),"contract":item.get("typeContratLibelle") or item.get("typeContrat", ""),"work_time":item.get("dureeTravailLibelle", ""),"description":item.get("description", ""),"published_at":str(item.get("dateCreation", ""))[:10],"source_url":origin.get("urlOrigine") or f"https://candidat.francetravail.fr/offres/recherche/detail/{item.get('id','')}"}

class SireneConnector:
    BASE = "https://api.insee.fr/api-sirene/3.11/siret"
    terms_url = "https://api.insee.fr/catalogue/"
    robots_url = "https://api.insee.fr/robots.txt"

    def __init__(self, token: str): self.token = token.strip()

    def search_loire(self, query="", workforce="", limit=50):
        if not self.token: raise RuntimeError("Clé API INSEE absente : connecteur non activé")
        limit=max(1,min(int(limit),200)); clauses=["codePostalEtablissement:42*", "etatAdministratifEtablissement:A"]
        if query: clauses.append(f'denominationUniteLegale:"{query.replace(chr(34), "")}"')
        if workforce: clauses.append(f"trancheEffectifsEtablissement:{workforce}")
        url=self.BASE+"?"+urllib.parse.urlencode({"q":" AND ".join(clauses),"nombre":limit})
        req=urllib.request.Request(url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/json","User-Agent":"CarnetEmploi42/1.0"})
        payload = _request_json(req, "INSEE")
        return [self.normalize(x) for x in payload.get("etablissements",[])]

    @staticmethod
    def normalize(x):
        u=x.get("uniteLegale",{}); a=x.get("adresseEtablissement",{})
        return {"siren":x.get("siren"),"siret":x.get("siret"),"company":u.get("denominationUniteLegale") or u.get("nomUniteLegale") or "Sans dénomination", "name":x.get("enseigne1Etablissement") or u.get("denominationUniteLegale") or "Établissement", "address":" ".join(str(a.get(k,"")) for k in ("numeroVoieEtablissement","typeVoieEtablissement","libelleVoieEtablissement")).strip(), "postcode":a.get("codePostalEtablissement",""), "city":a.get("libelleCommuneEtablissement",""), "workforce":x.get("trancheEffectifsEtablissement") or "Non renseigné", "active":x.get("etatAdministratifEtablissement")=="A"}


def _request_json(request, service: str) -> dict:
    """Traduit les pannes réseau/API en message compréhensible, sans exposer les secrets."""
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError(f"Identifiants {service} refusés. Vérifiez votre configuration") from exc
        raise RuntimeError(f"Le service {service} répond avec l'erreur HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Le service {service} est temporairement inaccessible. Vérifiez la connexion Internet") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"La réponse du service {service} est illisible") from exc


EXTERNAL_JOB_HOSTS = {
    "fr.indeed.com": "Indeed",
    "www.indeed.com": "Indeed",
    "indeed.com": "Indeed",
    "www.hellowork.com": "HelloWork",
    "hellowork.com": "HelloWork",
    "www.meteojob.com": "Meteojob",
    "meteojob.com": "Meteojob",
    "www.apec.fr": "Apec",
    "apec.fr": "Apec",
    "www.cadremploi.fr": "Cadremploi",
    "cadremploi.fr": "Cadremploi",
    "www.monster.fr": "Monster",
    "monster.fr": "Monster",
    "fr.linkedin.com": "LinkedIn",
    "www.welcometothejungle.com": "Welcome to the Jungle",
    "welcometothejungle.com": "Welcome to the Jungle",
}
EXTERNAL_USER_AGENT = "CarnetEmploi42/1.0 (+import manuel d'une offre)"
MAX_EXTERNAL_PAGE_BYTES = 2 * 1024 * 1024
SEARCH_PROVIDERS = {
    "indeed": {"name":"Indeed","host":"fr.indeed.com","url":"https://fr.indeed.com/jobs?q={keyword}&l={city}","paths":("/viewjob", "/rc/clk")},
    "hellowork": {"name":"HelloWork","host":"www.hellowork.com","url":"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={keyword}&l={city}","paths":("/fr-fr/emplois/",)},
    "meteojob": {"name":"Meteojob","host":"www.meteojob.com","url":"https://www.meteojob.com/jobs?what={keyword}&where={city}","paths":("/jobs/",)},
    "monster": {"name":"Monster","host":"www.monster.fr","url":"https://www.monster.fr/emploi/recherche?q={keyword}&where={city}","paths":("/emploi/offre", "/offre-demploi/")},
}


class _JobPostingParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.capture=False; self.parts=[]; self.documents=[]
    def handle_starttag(self, tag, attrs):
        attributes=dict(attrs)
        if tag.lower()=="script" and "ld+json" in attributes.get("type","").lower(): self.capture=True; self.parts=[]
    def handle_data(self, data):
        if self.capture: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag.lower()=="script" and self.capture:
            self.capture=False
            try: self.documents.append(json.loads("".join(self.parts)))
            except json.JSONDecodeError: pass


class _OfferLinkParser(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="a":
            href=dict(attrs).get("href","").strip()
            if href: self.links.append(href)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        target=urljoin(request.full_url,new_url)
        if urlparse(target).scheme!="https" or urlparse(target).hostname not in EXTERNAL_JOB_HOSTS or urlparse(target).port not in {None,443}:
            raise RuntimeError("La redirection de la page d'offre n'est pas autorisée")
        return super().redirect_request(request,file_pointer,code,message,headers,target)


class ExternalJobPageConnector:
    """Import manuel d'une page autorisée exposant un JobPosting JSON-LD."""
    def import_url(self, url: str) -> dict:
        parsed=urlparse(str(url).strip())
        if parsed.scheme!="https" or parsed.hostname not in EXTERNAL_JOB_HOSTS or parsed.port not in {None,443} or parsed.username or parsed.password:
            raise ValueError("Utilisez le lien HTTPS d'un site d'emploi pris en charge")
        service=EXTERNAL_JOB_HOSTS[parsed.hostname]
        if not self._robots_allowed(parsed.hostname,url,service):
            raise RuntimeError(f"{service} n'autorise pas l'import automatique de cette page. Saisissez l'offre manuellement")
        content=self._read_page(url,service)
        parser=_JobPostingParser(); parser.feed(content.decode("utf-8",errors="replace"))
        posting=self._find_posting(parser.documents)
        if not posting: raise ValueError("Aucune offre structurée JobPosting n'a été trouvée sur cette page")
        normalized=self.normalize(posting,url,service)
        if not normalized["title"]: raise ValueError("La page externe ne fournit pas d'intitulé de poste")
        return normalized

    def search(self, keyword: str, city: str, providers: list[str], limit=10) -> dict:
        keyword=str(keyword).strip(); city=str(city).strip()
        if not keyword: raise ValueError("Indiquez un métier ou des mots-clés")
        if not isinstance(providers,list) or not providers: raise ValueError("Sélectionnez au moins un site d'emploi")
        unknown=set(providers)-set(SEARCH_PROVIDERS)
        if unknown: raise ValueError("Site de recherche non autorisé")
        limit=max(1,min(int(limit),10)); links=[]; warnings=[]
        for provider in dict.fromkeys(providers):
            spec=SEARCH_PROVIDERS[provider]; search_url=spec["url"].format(keyword=urllib.parse.quote_plus(keyword),city=urllib.parse.quote_plus(city))
            try:
                if not self._robots_allowed(spec["host"],search_url,spec["name"]):
                    warnings.append(f"{spec['name']} refuse la recherche automatisée dans robots.txt"); continue
                parser=_OfferLinkParser(); parser.feed(self._read_page(search_url,spec["name"]).decode("utf-8",errors="replace"))
                for href in parser.links:
                    candidate=urljoin(search_url,href); parsed=urlparse(candidate)
                    if parsed.hostname==spec["host"] and any(parsed.path.startswith(path) for path in spec["paths"]):
                        clean=parsed._replace(fragment="").geturl()
                        if clean not in links: links.append(clean)
            except (RuntimeError,ValueError) as exc: warnings.append(f"{spec['name']} : {exc}")
        offers=[]
        for link in links:
            if len(offers)>=limit: break
            try: offers.append(self.import_url(link))
            except (RuntimeError,ValueError) as exc: warnings.append(f"{EXTERNAL_JOB_HOSTS.get(urlparse(link).hostname,'Site')} : {exc}")
        return {"offers":offers,"warnings":warnings,"links_found":len(links)}

    @staticmethod
    def _robots_allowed(host,url,service):
        robots_url=f"https://{host}/robots.txt"; robots=urllib.robotparser.RobotFileParser(); robots.set_url(robots_url)
        try:
            request=urllib.request.Request(robots_url,headers={"User-Agent":EXTERNAL_USER_AGENT})
            with urllib.request.build_opener(_SafeRedirectHandler()).open(request,timeout=10) as response:
                robots.parse(response.read(256*1024).decode("utf-8",errors="replace").splitlines())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc: raise RuntimeError(f"Impossible de vérifier les règles robots.txt de {service}") from exc
        return robots.can_fetch(EXTERNAL_USER_AGENT,url)

    @staticmethod
    def _read_page(url,service):
        request=urllib.request.Request(url,headers={"User-Agent":EXTERNAL_USER_AGENT,"Accept":"text/html,application/xhtml+xml"})
        try:
            with urllib.request.build_opener(_SafeRedirectHandler()).open(request,timeout=20) as response:
                content=response.read(MAX_EXTERNAL_PAGE_BYTES+1); final=response.geturl()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc: raise RuntimeError(f"La page {service} est temporairement inaccessible") from exc
        if len(content)>MAX_EXTERNAL_PAGE_BYTES: raise ValueError("La page externe est trop volumineuse")
        if urlparse(final).hostname not in EXTERNAL_JOB_HOSTS: raise RuntimeError("La page a redirigé vers un domaine non autorisé")
        return content

    @classmethod
    def _find_posting(cls, documents):
        for document in documents:
            candidates=document if isinstance(document,list) else document.get("@graph",[document]) if isinstance(document,dict) else []
            for candidate in candidates:
                types=candidate.get("@type",[]) if isinstance(candidate,dict) else []
                if (types=="JobPosting" or "JobPosting" in types): return candidate
        return None

    @staticmethod
    def normalize(item,url,service):
        organization=item.get("hiringOrganization") or {}; location=item.get("jobLocation") or {}
        if isinstance(location,list): location=location[0] if location else {}
        address=location.get("address") or {} if isinstance(location,dict) else {}
        city=address.get("addressLocality","") if isinstance(address,dict) else ""
        description=re.sub(r"<[^>]+>"," ",str(item.get("description","")))
        contract=item.get("employmentType",""); contract=", ".join(str(value) for value in contract) if isinstance(contract,list) else str(contract)
        return {"source":service,"source_url":url,"reference":str(item.get("identifier",{}).get("value","") if isinstance(item.get("identifier"),dict) else item.get("identifier","")),"title":str(item.get("title","")).strip(),"company":str(organization.get("name","") if isinstance(organization,dict) else organization).strip() or "Entreprise non renseignée","city":str(city).strip(),"contract":contract.strip(),"description":re.sub(r"\s+"," ",description).strip(),"published_at":str(item.get("datePosted",""))[:10]}
