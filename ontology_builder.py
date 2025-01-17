import sys
from datetime import datetime
from namespaces import Term
from namespace_registry import NamespaceRegistry as ns
from ApiCommon import log_it, split_string, get_onto_preferred_prefix
from sparql_client import EndpointClient
from tree_functions import Tree
from databases import Database, Databases, get_db_category_IRI
from concept_term import ConceptTermData


#-------------------------------------------------
class OntologyBuilder:
#-------------------------------------------------

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def __init__(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # load info from data_in used later by describe...() functions
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.ctd = ConceptTermData()
        self.prefixes = list()
        for space in ns.namespaces: self.prefixes.append(space.getTtlPrefixDeclaration())

        lines = list()
        for space in ns.namespaces: lines.append(space.getSparqlPrefixDeclaration())
        rqPrefixes = "\n".join(lines)
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # store queries used to retrieve ranges and domains of properties from sparql endpoint
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.client = EndpointClient("http://localhost:8890/sparql")
        self.domain_query_template = rqPrefixes + """
            select ?prop ?value (count(distinct ?s) as ?count) where {
                values ?prop { $prop }
                ?s ?prop ?o .
                ?s rdf:type ?value .
            } group by ?prop ?value"""        
        self.range_query_template = rqPrefixes + """
            select  ?prop ?value (count(*) as ?count) where {
                values ?prop { $prop }
                ?s ?prop ?o .
                optional { ?o rdf:type ?cl . }
                BIND(
                IF (bound(?cl) , ?cl,  IF ( isIRI(?o), 'rdfs:Resource', datatype(?o))
                ) as ?value)
            } group by ?prop ?value"""
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # domain / ranges to remove
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.rdfs_domain_to_remove = dict()
        self.rdfs_domain_to_remove[ns.cello.accession] = { ns.skos.Concept }
        #self.rdfs_domain_to_remove[ns.cello.category] = { ns.skos.Concept, ns.owl.NamedIndividual  }
        self.rdfs_domain_to_remove[ns.cello.database] = { ns.skos.Concept  }
        self.rdfs_domain_to_remove[ns.cello.hasVersion] = { ns.owl.NamedIndividual }
        self.rdfs_domain_to_remove[ns.cello.shortname] = { ns.owl.NamedIndividual }
        self.rdfs_domain_to_remove[ns.cello.more_specific_than] = { ns.cello.Xref  }
        self.rdfs_range_to_remove = dict()
        self.rdfs_range_to_remove[ns.cello.xref] = { ns.skos.Concept }
        self.rdfs_range_to_remove[ns.cello.more_specific_than] = { ns.cello.Xref } 
        self.rdfs_range_to_remove[ns.cello.database] = { ns.owl.NamedIndividual, ns.cello.CelloConceptScheme } 
        #self.rdfs_range_to_remove[ns.cello.genomeModificationMethod] = { ns.owl.NamedIndividual } 
        self.rdfs_range_to_remove[ns.cello.fromIndividualWithSex] = { ns.owl.NamedIndividual }
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # add description of terms (subClasses, ...) to terms in namespaces
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.describe_cell_line_and_subclasses()
        self.describe_genetic_characteristics_and_subclasses()
        self.describe_genome_editing_method_and_subclasses()
        self.describe_sequence_variation_and_subclasses()
        self.describe_publication_hierarchy_based_on_fabio_no_redundancy()
        self.describe_terminology_database_and_subclasses()
        self.describe_cell_line_properties()
        self.describe_organization_related_terms()
        self.describe_misc_classes()
        self.describe_ranges_and_domains()
        self.describe_annotation_properties()
        self.describe_labels()
        self.describe_comments()
        

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def build_class_tree(self, local_only=False):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # NOW build tree with (local) child - parent relationships based on rdfs:subClassOf()
        edges = dict()
        relevant_namespaces = ns.namespaces
        if local_only: relevant_namespaces = [ ns.cello ]
        for space in relevant_namespaces:
            for term_id in space.terms:
                term: Term = space.terms[term_id]
                if not term.isA(ns.owl.Class): continue
                for parent_iri in term.props.get(ns.rdfs.subClassOf) or set():
                    if parent_iri.startswith(ns.cello.pfx) or not local_only:
                        #print("DEBUG tree", term.iri, "has parent", parent_iri)
                        if term.iri in edges:
                            log_it(f"WARNING, multiple parents for {term.iri}:  parent {edges[term.iri]} replaced with {parent_iri}")
                        edges[term.iri] = parent_iri
        self.tree = Tree(edges)

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_related_terms(self, parent_class_IRI, term, termIsClass=True):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    # CAUTION: for owl:equivalentProperty triples to be parsed by protege, widoco, ...
    # the property appearing in the object of the triple must be declared
    # in the ontology as the same property type (ObjectProperty, DatatypeProperty, ...) 
    # as the property appearing in the subject in the triple
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        #print("parent_clss_IRI", parent_class_IRI)
        for rel_key in term:
            rel_prop = None
            if   rel_key == "EQ": 
                if termIsClass: rel_prop = ns.owl.equivalentClass
                else: rel_prop = ns.owl.equivalentProperty
            elif   rel_key == "<<": 
                if termIsClass: rel_prop = ns.rdfs.subClassOf
                else: rel_prop = ns.rdfs.subPropertyOf
            elif rel_key == "BR": rel_prop = ns.skos.broadMatch
            elif rel_key == "CL": rel_prop = ns.skos.closeMatch
            if rel_prop is None:log_it("ERROR, unknown relation in concept_term:", term)
            for el in term[rel_key]:
                #print(". el:", el)
                iri = self.ctd.getTermIRI(el)
                #print(". iri: ", iri)
                prefixed_iri = ns.getPrefixedIRI(iri)
                #print(" .prefixed IRI")
                if prefixed_iri is None:
                    ns.describe(parent_class_IRI, rel_prop, iri)
                else:
                    el_id = prefixed_iri.split(":")[1]
                    # properties in wd: are already explicitly registered as Object/or/DatatypeProperty in their namespace
                    ns.getNamespace(iri).registerTerm(el_id)
                    if termIsClass: ns.describe(prefixed_iri, ns.rdf.type, ns.owl.Class) # WARNING: this might be a lie in some cases
                    el_lbl = self.ctd.getTermLabel(el)
                    ns.describe(prefixed_iri, ns.rdfs.label, ns.xsd.string(el_lbl))
                    ns.describe(parent_class_IRI, rel_prop, prefixed_iri)


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_annotation_properties(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        ns.describe(ns.cello.hasVersion, ns.rdfs.subPropertyOf, ns.dcterms.hasVersion)
        ns.describe(ns.cello.modified, ns.rdfs.subPropertyOf, ns.dcterms.modified)
        ns.describe(ns.cello.created, ns.rdfs.subPropertyOf, ns.dcterms.created)


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_organization_related_terms(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # describing our own terms as subClass/Prop of terms defined elsewhere 
        # instead of simply using these external terms allow to give them a domain / range
        # and additional semantic relationships to other terms
        ns.describe(ns.cello.name, ns.rdfs.subPropertyOf, ns.dcterms.title)
        ns.describe(ns.cello.memberOf, ns.rdfs.subPropertyOf, ns.schema.memberOf)
        ns.describe(ns.cello.city, ns.rdfs.subPropertyOf, ns.schema.location)
        ns.describe(ns.cello.country, ns.rdfs.subPropertyOf, ns.schema.location)


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_genetic_characteristics_and_subclasses(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 

        # OBI:0001404 - genetic characteristics information
        # OBI:0001364 - genetic alteration information = superclass for seq var + gen.int + gen.ko)
        # OBI:0001225 - genetic population background information

        ns.describe(ns.OBI._0001225, ns.rdfs.subClassOf, ns.OBI._0001404)     
        ns.describe(ns.OBI._0001364, ns.rdfs.subClassOf, ns.OBI._0001404)     
        ns.describe(ns.cello.GeneticIntegration, ns.rdfs.subClassOf, ns.OBI._0001364)
        ns.describe(ns.cello.GeneKnockout, ns.rdfs.subClassOf, ns.OBI._0001364)

        # subclass OBI:0002769 karyotype information  
        # ...


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_genome_editing_method_and_subclasses(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        ns.describe(ns.FBcv._0003008, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.NCIt.C17262, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.NCIt.C44386, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0001152, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0001154, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0002626, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0003134, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0003135, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0003137, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.OBI._0600059, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.BacHomologousRecombination, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.CreLoxp, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.CrisprCas9N, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.EbvBasedVectorSirnaKnockdown, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.FloxingCreRecombination, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.GeneTargetedKoMouse, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.HelperDependentAdenoviralVector, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.HomologousRecombination, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.KnockoutFirstConditional, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.KoMouse, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.KoPig, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.MirnaKnockdown, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.NullMutation, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.PElement, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.PiggybacTransposition, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.PrimeEditing, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.PromoterlessGeneTargeting, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.RecombinantAdenoAssociatedVirus, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.ShrnaKnockdown, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.SleepingBeautyTransposition, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.SpontaneousMutation, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TargetedIntegration, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TransductionTransfection, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TransfectionTransduction, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TransgenicFish, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TransgenicMouse, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)
        ns.describe(ns.cello.TransgenicRat, ns.rdfs.subClassOf, ns.OBI.GenomeModificationMethod)



    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_cell_line_properties(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        for celloPropIRI in self.ctd.getCelloTermKeys("CellLineProperties"):
            term = self.ctd.getCelloTerm("CellLineProperties", celloPropIRI)
            self.describe_related_terms(celloPropIRI, term, termIsClass=False)

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_ranges_and_domains(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.build_class_tree()

        for term_id in ns.cello.terms:
            term: Term = ns.cello.terms[term_id]
            if not term.isA(ns.rdf.Property): continue
            # gather domain classes
            log_it("DEBUG", "querying prop_name", term.iri, "domains")
            domain_dic = dict()
            domain_query = self.domain_query_template.replace("$prop", term.iri)
            response = self.client.run_query(domain_query)
            if not response.get("success"):
                log_it("ERROR", response.get("error_type"))
                log_it(response.get("error_msg"))
                sys.exit(2)
            rows = response.get("results").get("bindings")
            for row in rows:
                value = self.client.apply_prefixes(row.get("value").get("value"))
                count = int(row.get("count").get("value"))
                domain_dic[value]=count

            # simplify domain
            domain_set = set(domain_dic.keys())
            if len(domain_set)>3: 
                domain_set = self.tree.get_close_parent_set(domain_set)
            for domain_to_remove in self.rdfs_domain_to_remove.get(term.iri) or {}:
                domain_set = domain_set - { domain_to_remove }

            # gather range datatypes / classes
            log_it("DEBUG", "querying prop_name", term.iri, "ranges")
            range_dic = dict()
            range_query = self.range_query_template.replace("$prop", term.iri)
            response = self.client.run_query(range_query)
            if not response.get("success"):
                log_it("ERROR", response.get("error_type"))
                log_it(response.get("error_msg"))
                sys.exit(2)
            rows = response.get("results").get("bindings")
            for row in rows:
                value = self.client.apply_prefixes(row.get("value").get("value"))
                count = int(row.get("count").get("value"))
                range_dic[value]=count
            # ttl comment about domain classes found in data
            domain_comments = list()
            tmp = list()
            for k in domain_dic: tmp.append(f"{k}({domain_dic[k]})")
            for line in split_string(" ".join(tmp), 90):
                domain_comments.append("#   domain classes found in data: " + line)

            # simplify ranges
            range_set = set(range_dic.keys())
            if len(range_set)>3:
                range_set = self.tree.get_close_parent_set(range_set)
            # hack to replace xsd:date with rdfs:Literal to be OWL2 frienly                    
            if ns.xsd.dateDataType in range_set:
                range_set = range_set - { ns.xsd.dateDataType }
                range_set.add(ns.rdfs.Literal)
            for range_to_remove in self.rdfs_range_to_remove.get(term.iri) or {}:
                range_set = range_set - { range_to_remove } 
            # ttl comment about prop range
            range_comments = list()
            tmp = list()
            for k in range_dic: tmp.append(f"{k}({range_dic[k]})")
            for line in split_string(" ".join(tmp), 90):
                range_comments.append("#   range entities found in data: " + line)
            # check prop type
            prop_types = set() # we should have a single item in this set (otherwise OWL reasoners dislike it)
            for r in range_dic:
                if r.startswith("xsd:") or r == ns.rdfs.Literal: prop_types.add("owl:DatatypeProperty") 
                else: prop_types.add("owl:ObjectProperty") 
            if len(prop_types) != 1: 
                log_it("ERROR", term.iri, "has not one and only one type", prop_types)
            else:
                declared_types = term.props.get(ns.rdf.type) # also includes rdf:Property
                found_type = prop_types.pop()
                if found_type not in declared_types and ns.owl.AnnotationProperty not in declared_types: 
                    log_it("ERROR", term.iri, f"range declaration {declared_types} does not match data {found_type}")
                        
            for domain in domain_set: ns.describe(term.iri, ns.rdfs.domain, domain)
            for comment in domain_comments: ns.describe(term.iri, "domain_comments", comment)
            for range in range_set: ns.describe(term.iri, ns.rdfs.range, range)
            for comment in range_comments: ns.describe(term.iri, "range_comments", comment)



    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_publication_hierarchy_based_on_fabio_no_redundancy(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        
        # TODO: add relationshp with dcterms.Bibliographic.... ?

        # Publication hierarchy based on fabio Expression
        ns.describe( ns.cello.Publication,              ns.rdfs.subClassOf, ns.fabio.Expression)
        ns.describe( ns.fabio.Book,                     ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.BookChapter,              ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.JournalArticle,           ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.PatentDocument,           ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.ReportDocument,           ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.cello.TechnicalDocument,        ns.rdfs.subClassOf, ns.fabio.ReportDocument)
        ns.describe( ns.cello.MiscellaneousDocument,    ns.rdfs.subClassOf, ns.fabio.ReportDocument)
        ns.describe( ns.fabio.ConferencePaper,          ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.Thesis,                   ns.rdfs.subClassOf, ns.cello.Publication)
        ns.describe( ns.fabio.BachelorsThesis,               ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.fabio.MastersThesis,                 ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.fabio.DoctoralThesis,                ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.cello.MedicalDegreeThesis,           ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.cello.MedicalDegreeMasterThesis,     ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.cello.PrivaDocentThesis,             ns.rdfs.subClassOf, ns.fabio.Thesis)
        ns.describe( ns.cello.VeterinaryMedicalDegreeThesis, ns.rdfs.subClassOf, ns.fabio.Thesis)

        # Relationships with UniProt
        ns.describe( ns.cello.Publication,              ns.skos.closeMatch, ns.up.Published_Citation)
        ns.describe( ns.fabio.BookChapter,              ns.skos.closeMatch, ns.up.Book_Citation)  # book citation is for book chapter !
        ns.describe( ns.fabio.JournalArticle,            ns.skos.closeMatch, ns.up.Journal_Citation )
        ns.describe( ns.fabio.PatentDocument,                   ns.skos.closeMatch, ns.up.Patent_Citation)
        ns.describe( ns.fabio.Thesis,                   ns.skos.closeMatch, ns.up.Thesis_Citation)
        ns.describe( ns.fabio.BachelorsThesis,               ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.fabio.MastersThesis,                 ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.fabio.DoctoralThesis,               ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.cello.MedicalDegreeThesis,          ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.cello.MedicalDegreeMasterThesis,    ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.cello.PrivaDocentThesis,            ns.skos.broadMatch, ns.up.Thesis_Citation)
        ns.describe( ns.cello.VeterinaryMedicalDegreeThesis, ns.skos.broadMatch, ns.up.Thesis_Citation)



    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_misc_classes(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        for tk in self.ctd.getCelloTermKeys("MiscClasses"):
            term_data = self.ctd.getCelloTerm("MiscClasses", tk)
            self.describe_related_terms(tk, term_data, termIsClass=True)


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_terminology_database_and_subclasses(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # Describe root cellosaurus terminology class
        ns.describe(ns.cello.CelloConceptScheme, ns.rdfs.subClassOf, ns.skos.ConceptScheme)

        # Describe parent class of our Database class and equivalence to uniprot Database
        ns.describe(ns.cello.Database, ns.rdfs.subClassOf, ns.NCIt.C15426)
        ns.describe(ns.cello.Database, ns.owl.equivalentClass, ns.up.Database)
        
        # we add programmaticaly the subClassOf relationships between Database and its children
        # so that we can take advantage of close_parent_set() method during computation of domain / ranges of related properties
        databases = Databases()
        for k in databases.categories():
            cat = databases.categories()[k]
            cat_label = cat["label"]
            cat_IRI = get_db_category_IRI(cat_label)
            cat_id = cat_IRI.split(":")[1]
            ns.cello.registerClass(cat_id)
            ns.describe(cat_IRI, ns.rdfs.subClassOf, ns.cello.Database)
            ns.describe(cat_IRI, ns.rdfs.label, ns.xsd.string(cat_label))            


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_sequence_variation_and_subclasses(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # TODO: describe SequenceVariation

        ns.describe( ns.cello.GeneMutation, ns.rdfs.subClassOf, ns.cello.SequenceVariation )
        ns.describe( ns.cello.RepeatExpansion, ns.rdfs.subClassOf, ns.cello.GeneMutation  )
        ns.describe( ns.cello.SimpleMutation, ns.rdfs.subClassOf, ns.cello.GeneMutation  )
        ns.describe( ns.cello.UnexplicitMutation, ns.rdfs.subClassOf, ns.cello.GeneMutation  )
        ns.describe( ns.cello.GeneFusion, ns.rdfs.subClassOf, ns.cello.SequenceVariation  )
        ns.describe( ns.cello.GeneAmplification, ns.rdfs.subClassOf, ns.cello.SequenceVariation ) 
        ns.describe( ns.cello.GeneDuplication, ns.rdfs.subClassOf, ns.cello.GeneAmplification  )
        ns.describe( ns.cello.GeneTriplication, ns.rdfs.subClassOf, ns.cello.GeneAmplification  )
        ns.describe( ns.cello.GeneQuadruplication, ns.rdfs.subClassOf, ns.cello.GeneAmplification  )
        ns.describe( ns.cello.GeneExtensiveAmplification, ns.rdfs.subClassOf, ns.cello.GeneAmplification ) 
        ns.describe( ns.cello.GeneDeletion, ns.rdfs.subClassOf, ns.cello.SequenceVariation  )
        for tk in self.ctd.getCelloTermKeys("SequenceVariation"):
            term_data = self.ctd.getCelloTerm("SequenceVariation", tk)
            self.describe_related_terms(tk, term_data, termIsClass=True)


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_cell_line_and_subclasses(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # generate rdfs:subClass links between wd.CellLine and others
        for id in ns.wd.terms:
            term = ns.wd.terms[id]
            if "owl:Class" in term.props["rdf:type"]:
                if term.iri != ns.wd.CellLine:
                    ns.describe(term.iri, ns.rdfs.subClassOf, ns.wd.CellLine)

        # describe how CellLine classes relate in the universe 
        ns.describe(ns.wd.CellLine, ns.skos.closeMatch, "<http://purl.obolibrary.org/obo/CLO_0000031>")
        ns.describe(ns.wd.CellLine, ns.skos.closeMatch, "<http://id.nlm.nih.gov/mesh/D002460>")
        ns.describe(ns.wd.CellLine, ns.rdfs.seeAlso, "<https://www.cellosaurus.org/>")
        ns.describe(ns.wd.Q23058136, ns.skos.closeMatch, f"<{ns.OBI.url}0001906>" )
        ns.describe(ns.wd.Q107102664, ns.skos.closeMatch, f"<{ns.BTO.url}0001581>" )
        ns.describe(ns.wd.Q107102664, ns.skos.closeMatch, f"<{ns.CLO.url}0037279>" )
        ns.describe(ns.wd.Q27671617, ns.skos.closeMatch, f"<{ns.CLO.url}0009829>" )
        ns.describe(ns.wd.Q27554370, ns.skos.closeMatch, f"<{ns.BTO.url}0001926>" )
        ns.describe(ns.wd.Q27554370, ns.skos.closeMatch, f"<{ns.CLO.url}0036932>" )
        ns.describe(ns.wd.Q107103143, ns.skos.closeMatch, f"<{ns.CLO.url}0037307>" )
        ns.describe(ns.wd.Q27671698, ns.skos.closeMatch, f"<{ns.BTO.url}0005996>" )
        ns.describe(ns.wd.Q27555384, ns.skos.closeMatch, f"<{ns.OMIT.url}0003790>" )



    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_labels(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # non default labels for classes and properties
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        self.rdfs_label = dict()
        ns.describe(ns.cello.HLATyping, ns.rdfs.label, ns.xsd.string("HLA typing"))
        ns.describe(ns.cello.hlaTyping, ns.rdfs.label, ns.xsd.string("has HLA typing"))
        ns.describe(ns.cello.MabIsotype, ns.rdfs.label, ns.xsd.string("Monoclonal antibody isotype"))
        #ns.describe(ns.cello.MabTarget, ns.rdfs.label, ns.xsd.string("Monoclonal antibody target"))
        ns.describe(ns.cello.mabIsotype, ns.rdfs.label, ns.xsd.string("has monoclonal antibody isotype"))
        ns.describe(ns.cello.mabTarget, ns.rdfs.label, ns.xsd.string("has monoclonal antibody target"))
        ns.describe(ns.cello.hasPMCId, ns.rdfs.label, ns.xsd.string("has PMC identifier"))
        ns.describe(ns.fabio.hasPubMedCentralId, ns.rdfs.label, ns.xsd.string("has PMC identifier"))
        ns.describe(ns.cello.hasPubMedId, ns.rdfs.label, ns.xsd.string("has PubMed identifier"))
        ns.describe(ns.fabio.hasPubMedId, ns.rdfs.label, ns.xsd.string("has PubMed identifier"))
        ns.describe(ns.cello.hasDOI, ns.rdfs.label, ns.xsd.string("has DOI identifier"))
        ns.describe(ns.cello.issn13, ns.rdfs.label, ns.xsd.string("has ISSN13 identifier"))
        ns.describe(ns.cello.msiValue, ns.rdfs.label, ns.xsd.string("has microsatellite instability value"))
        ns.describe(ns.cello.productId, ns.rdfs.label, ns.xsd.string("product identifier"))
        ns.describe(ns.cello.xref, ns.rdfs.label, ns.xsd.string("has cross-reference"))
        ns.describe(ns.cello.Xref, ns.rdfs.label, ns.xsd.string("Cross-reference"))
        ns.describe(ns.cello.CelloConceptScheme, ns.rdfs.label, ns.xsd.string("Cellosaurus concept scheme"))

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def describe_comments(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        # comments for classes

        # comments for props
        ns.describe(ns.cello.recommendedName, ns.rdfs.comment, ns.xsd.string("Most frequently the name of the cell line as provided in the original publication"))
        ns.describe(ns.cello.alternativeName, ns.rdfs.comment, ns.xsd.string("Different synonyms for the cell line, including alternative use of lower and upper cases characters. Misspellings are not included in synonyms"))
        ns.describe(ns.cello.more_specific_than, ns.rdfs.comment, ns.xsd.string("Links two concepts. The subject concept is more specific than the object concept. The semantics is the similar to the skos:broader property but its label less ambiguous."))
        ns.describe(ns.cello.CelloConceptScheme, ns.rdfs.comment, ns.xsd.string("Class of cellosaurus terminologies containing some concepts used for annotating cell lines."))




    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_onto_header(self, version="alpha"):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        lines = list()

        # set last modification date for ontology
        now = datetime.now()
        date_string = now.strftime("%Y-%m-%d")
        
        # set ontology URL
        onto_url = "<" + self.get_onto_url() + ">"
        
        # set ontology description
        onto_descr = """The Cellosaurus ontology describes the concepts used to build the Cellosaurus knowledge resource on cell lines. 
        The Cellosaurus attempts to describe all cell lines used in biomedical research."""
        
        # set ontology abstract
        onto_abstract = onto_descr

        # set preferred prefix for ontology
        onto_prefix = "cls" 

        # set ontology introduction
        onto_intro = onto_descr

        # Note: all the prefixes are declared in namespace.py but not necessarily all the properties because used only once...
        lines.append(onto_url)
        lines.append("    a " + ns.owl.Ontology + " ;")
        lines.append("    " + ns.rdfs.label + " " + ns.xsd.string("Cellosaurus ontology") + " ;")
        lines.append("    " + ns.dcterms.created + " " + ns.xsd.date("2024-07-30") + " ;")
        lines.append("    " + ns.dcterms.modified + " " + ns.xsd.date(date_string) + " ;")
        lines.append("    " + ns.dcterms.description + " " + ns.xsd.string3(onto_descr) + " ;")
        lines.append("    " + ns.dcterms.license + " <http://creativecommons.org/licenses/by/4.0> ;")
        lines.append("    " + ns.dcterms.title + " " + ns.xsd.string("Cellosaurus ontology") + " ;")
        lines.append("    " + ns.dcterms.hasVersion + " " + ns.xsd.string(version) + " ;")
        lines.append("    " + ns.owl.versionInfo + " " + ns.xsd.string(version) + " ;")
        lines.append("    " + ns.dcterms.abstract + " " + ns.xsd.string3(onto_abstract) + " ;")
        lines.append("    " + ns.vann.preferredNamespacePrefix + " " + ns.xsd.string(get_onto_preferred_prefix()) + " ;")
        lines.append("    " + ns.bibo.status + " <http://purl.org/ontology/bibo/status/published> ;")
        lines.append("    " + ns.widoco.introduction + " " + ns.xsd.string3(onto_intro) + " ;")
        lines.append("    " + ns.rdfs.seeAlso + " " + ns.help.IRI("index-en.html") + " ;")      
        lines.append("    " + ns.widoco.rdfxmlSerialization + " " + ns.help.IRI("ontology.owl") + " ;")      
        lines.append("    " + ns.widoco.ntSerialization + " " + ns.help.IRI("ontology.nt") + " ;")      
        lines.append("    " + ns.widoco.turtleSerialization + " " + ns.help.IRI("ontology.ttl") + " ;")      
        lines.append("    " + ns.widoco.jsonldSerialization + " " + ns.help.IRI("ontology.jsonld") + " ;")

        # shacl declaration of prefixes for void tools        
        for elem in ns.namespaces:
            lines.append("    " + ns.sh.declare + " [ ")
            pfx = elem.pfx
            if pfx == "": pfx = "cello"
            lines.append("        " + ns.sh._prefix  + " " + ns.xsd.string(pfx) + " ;")
            lines.append("        " + ns.sh.namespace  + " " + ns.xsd.string(elem.url) + " ;")
            lines.append("    ] ;")

        lines.append("    .")

        lines.extend(self.get_query_examples())

        lines.append("")
        return lines

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_query_examples(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        lines = list()
        px = get_onto_preferred_prefix()
        lines.append("")
        lines.append(px + ":Query_001 a sh:SPARQLExecutable ;")
        lines.append("""    rdfs:comment "Count of cell lines" ; """)
        lines.append("""    sh:select "select (count(*) as ?cnt) where { ?cl rdf:type / rdfs:subClassOf cello:CellLine . }" ; """)
        lines.append("    .")
        lines.append("")
        lines.append(px +":Query_002 a sh:SPARQLExecutable ;")
        lines.append("""    rdfs:comment "Count of publication citations" ; """)
        lines.append("""    sh:select "select (count(*) as ?cnt) where { ?cl rdf:type / rdfs:subClassOf cello:Publication . }" ; """)
        lines.append("    .")
        lines.append("")
        return lines


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_onto_url(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        onto_url = ns.cello.url
        if onto_url.endswith("#"): onto_url = onto_url[:-1]
        return onto_url

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_onto_prefixes(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        return self.prefixes


    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_imported_terms(self):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        lines = list()
        
        allButCello = list(ns.namespaces)
        
        # remove basic ones
        allButCello.remove(ns.xsd)
        allButCello.remove(ns.rdf)
        allButCello.remove(ns.rdfs)
        allButCello.remove(ns.owl)
        allButCello.remove(ns.sh)
        allButCello.remove(ns.widoco)
        #allButCello.remove(ns.dcterms) # only some terms are hidden for the moment
        
        # remove namespaces for our data
        allButCello.remove(ns.cello)
        allButCello.remove(ns.cvcl)
        allButCello.remove(ns.db)
        allButCello.remove(ns.orga)
        allButCello.remove(ns.xref)
        
        # remove irrelevant ones
        allButCello.remove(ns.pubmed)

        for nspace in allButCello: lines.extend(self.get_terms(nspace))
        return lines
    

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_terms(self, nspace, owlType=None):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        lines = list()
        for id in nspace.terms:
            term: Term = nspace.terms[id]
            if owlType is None or term.isA(owlType): lines.extend(term.ttl_lines())
        return lines
    
    
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_onto_terms(self, owlType=None):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        return self.get_terms(ns.cello, owlType)
    

    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
    def get_onto_pretty_ttl_lines(self, version):
    # - - - - - - - - - - - - - - - - - - - - - - - - - - - - 
        lines = list()    
        lines.extend(self.get_onto_prefixes())
        lines.append("\n#\n# Ontology properties\n#\n")
        lines.extend(self.get_onto_header(version))
        lines.append("#\n# External terms used in ontology\n#\n")
        lines.extend(self.get_imported_terms())
        lines.append("#\n# Classes defined in ontology\n#\n")
        lines.extend(self.get_onto_terms(ns.owl.Class))
        lines.append("#\n# Annotation Properties used in ontology\n#\n")
        lines.extend(self.get_onto_terms(ns.owl.AnnotationProperty))
        lines.append("#\n# Object Properties used in ontology\n#\n")
        lines.extend(self.get_onto_terms(ns.owl.ObjectProperty))
        lines.append("#\n# Datatype Properties used in ontology\n#\n")
        lines.extend(self.get_onto_terms(ns.owl.DatatypeProperty))
        return lines


# =============================================
if __name__ == '__main__':
# =============================================

    ob = OntologyBuilder()
    lines = ob.get_onto_pretty_ttl_lines("dev version")
    for l in lines: print(l)
