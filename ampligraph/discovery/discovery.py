import logging
import numpy as np
from sklearn.cluster import DBSCAN
import networkx as nx

from ..evaluation import evaluate_performance, filter_unseen_entities

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def discover_facts(X, model, top_n=10, strategy='exhaustive',
                   max_candidates=.3, target_rel=None,
                   corruption_entities=None,
                   max_negatives=100,
                   seed=0):

    """ Discover new facts from an existing knowledge graph.


        Parameters
        ----------

        X : ndarray, shape [n, 3]
            The input knowledge graph used to train ``model``.
        model : EmbeddingModel
            The trained model that will be used to score candidate facts.
            ``model`` must have been previously trained on ``X``.
        top_n : int
            The cutoff position in ranking to consider a candidate triple
            as true positive.
        strategy: string
            The candidates generation strategy. Choose from 'exhaustive',
            'random_uniform'.
        max_candidates: int or float
            The maximum numbers of candidates generated by 'strategy'.
            Can be an absolute number or a percentage [0,1].
        target_rel : str
            Target relation to focus on. The function will discover facts only
            for that specific relation type.
            If None, the function attempts to discover new facts for all
            relation types in the graph.
        corruption_entities : ndarray, shape [n]
            A list of entities that will used in the evaluation step to
            generate negatives that will be ranked against
            candidate statements. If None, all entities will be used.
        max_negatives : int
            The maximum number of corrupted triples to generate during
            evaluation. The higher this value,
            the harder the ranking task.
        seed : int
            Seed to use for reproducible results.


        Returns
        -------
        X_pred : ndarray, shape [n, 3]
            A list of new facts predicted to be true.


        Examples
        --------
        >>> import numpy as np
        >>> from ampligraph.discovery import discover_facts

        >>> X = np.array([['a', 'y', 'b'],
        >>>               ['b', 'y', 'a'],
        >>>               ['a', 'y', 'c'],
        >>>               ['c', 'y', 'a'],
        >>>               ['a', 'y', 'd'],
        >>>               ['c', 'y', 'd'],
        >>>               ['b', 'y', 'c'],
        >>>               ['f', 'y', 'e']])
        >>>

                    #TODO

        >>> X_pred = discover_facts(X)
        >>> ([['a', 'y', 'e'],
        >>>  ['f', 'y', 'a'],
        >>>  ['c', 'y', 'e']])

    """

    if not model.is_fitted:
        msg = 'Model is not fitted.'
        logger.error(msg)
        raise ValueError(msg)

    if not model.is_fitted_on(X):
        msg = 'Model might not be fitted on this data.'
        logger.warning(msg)
        # raise ValueError(msg)

    if strategy not in ['exhaustive', 'random_uniform']:
        msg = '%s is not a valid strategy.' % strategy
        logger.error(msg)
        raise ValueError(msg)

    if target_rel is None:
        msg = 'No target relation specified. Using all relations to ' \
              'generate candidate statements.'
        logger.info(msg)
    else:
        if target_rel not in model.rel_to_idx.keys():
            msg = 'Target relation not found in model.'
            logger.error(msg)
            raise ValueError(msg)

    # Set random seed
    np.random.seed(seed)

    # Remove unseen entities
    X_filtered = filter_unseen_entities(X, model)

    if target_rel is None:
        rel_list = [x for x in model.rel_to_idx.keys()]
    else:
        rel_list = [target_rel]

    discoveries = []

    # Iterate through relations
    for relation in rel_list:

        logger.debug('Generating candidates for relation: %s' % relation)
        candidate_generator = generate_candidates(X_filtered, strategy,
                                                  relation, max_candidates,
                                                  seed=seed)

        for candidates in candidate_generator:

            logger.debug('Generated %d candidate statements.' %
                         len(candidates))

            # Get ranks of candidate statements
            ranks = evaluate_performance(candidates,
                                         model=model,
                                         filter_triples=X,
                                         use_default_protocol=True,
                                         verbose=True)

            # Select candidate statements within the top_n predicted ranks
            # standard protocol evaluates against corruptions on both sides,
            # we just average the ranks here
            num_ranks = len(ranks) // 2
            s_corruption_ranks = ranks[:num_ranks]
            o_corruption_ranks = ranks[num_ranks:]

            avg_ranks = np.mean([s_corruption_ranks, o_corruption_ranks],
                                axis=0)

            preds = np.array(avg_ranks) >= top_n

            discoveries.append(candidates[preds])

    logger.info('Discovered %d facts' % len(discoveries))

    return np.hstack(discoveries)


def generate_candidates(X, strategy, target_rel, max_candidates,
                        consolidate_sides=False, seed=0):
    """ Generate candidate statements from an existing knowledge
        graph using a defined strategy.

        Parameters
        ----------

        strategy: string
            The candidates generation strategy.
            - 'exhaustive' : generates all possible candidates given the ```target_rel``` and
                ```consolidate_sides``` parameter.
            - 'random_uniform' : generates N candidates (N <= max_candidates) based on a uniform random sampling of
                head and tail entities.
            - 'entity_frequency' : generates candidates by sampling entities with low frequency.
            - 'graph_degree' : generates candidates by sampling entities with a low graph degree.
            - 'cluster_coefficient' : generates candidates by sampling entities with a low clustering coefficient.
            - 'cluster_triangles' : generates candidates by sampling entities with a low number of cluster triangles.
            - 'cluster_squares' : generates candidates by sampling entities with a low number of cluster squares.
        max_candidates: int or float
            The maximum numbers of candidates generated by 'strategy'.
            Can be an absolute number or a percentage [0,1].
            This does not guarantee the number of candidates generated.
        target_rel : str
            Target relation to focus on. The function will generate candidate
             statements only with this specific relation type.
        consolidate_sides: bool
            If True will generate candidate statements as a product of
            unique head and tail entities, otherwise will
            consider head and tail entities separately. Default: False.
        seed : int
            Seed to use for reproducible results.

        Returns
        -------
        X_candidates : ndarray, shape [n, 3]
            A list of candidate statements.


        Examples
        --------
        >>> import numpy as np
        >>> from ampligraph.discovery.discovery import generate_candidates
        >>>
        >>> X = np.array([['a', 'y', 'b'],
        >>>               ['b', 'y', 'a'],
        >>>               ['a', 'y', 'c'],
        >>>               ['c', 'y', 'a'],
        >>>               ['a', 'y', 'd'],
        >>>               ['c', 'y', 'd'],
        >>>               ['b', 'y', 'c'],
        >>>               ['f', 'y', 'e']])

        >>> X_candidates = generate_candidates(X, strategy='graph_degree',
        >>>                                     target_rel='y', max_candidates=3)
        >>> ([['a', 'y', 'e'],
        >>>  ['f', 'y', 'a'],
        >>>  ['c', 'y', 'e']])

    """

    if strategy not in ['random_uniform', 'exhaustive', 'entity_frequency',
                        'graph_degree', 'cluster_coefficient',
                        'cluster_triangles', 'cluster_squares']:
        msg = '%s is not a valid candidate generation strategy.' % strategy
        raise ValueError(msg)

    if target_rel not in np.unique(X[:, 1]):
        # No error as may be case where target_rel is not in X
        msg = 'Target relation is not found in triples.'
        logger.warning(msg)

    if not isinstance(max_candidates, (float, int)):
        msg = 'Parameter max_candidates must be a float or int.'
        raise ValueError(msg)

    if max_candidates <= 0:
        msg = 'Parameter max_candidates must be a positive integer ' \
              'or float in range (0,1].'
        raise ValueError(msg)

    if isinstance(max_candidates, float):
        _max_candidates = int(max_candidates * len(X))
    elif isinstance(max_candidates, int):
        _max_candidates = max_candidates

    # Set random seed
    np.random.seed(seed)

    # Get entities linked with this relation
    if consolidate_sides:
        e_s = np.unique(np.concatenate((X[:, 0], X[:, 2])))
        e_o = e_s
    else:
        e_s = np.unique(X[:, 0])
        e_o = np.unique(X[:, 2])

    logger.info('Generating candidates using {} strategy.'.format(strategy))

    def _filter_candidates(X_candidates, X):

        # Filter statements that are in X
        X_candidates = _setdiff2d(X_candidates, X)

        # Filter statements that are ['x', rel, 'x']
        keep_idx = np.where(X_candidates[:, 0] != X_candidates[:, 2])
        return X_candidates[keep_idx]

    if strategy == 'exhaustive':

        # Exhaustive, generate all combinations of subject and object
        # entities for target_rel

        # Generate all combinates for a single entity at each iteration
        for ent in e_s:
            X_candidates = np.array(np.meshgrid(ent, target_rel, e_o)).T.reshape(-1, 3)

            X_candidates = _filter_candidates(X_candidates, X)

            yield X_candidates

    elif strategy == 'random_uniform':

        # Take close to sqrt of max_candidates so that:
        #   len(meshgrid result) == max_candidates
        sample_size = int(np.sqrt(_max_candidates))

        sample_e_s = np.random.choice(e_s, size=sample_size, replace=False)
        sample_e_o = np.random.choice(e_o, size=sample_size, replace=False)

        X_candidates = np.array(np.meshgrid(sample_e_s, target_rel, sample_e_o)).T.reshape(-1, 3)

        X_candidates = _filter_candidates(X_candidates, X)

        yield X_candidates

    elif strategy == 'entity_frequency':

        # Get entity counts and sort them in ascending order
        ent_counts = np.array(np.unique(X[:, [0, 2]], return_counts=True)).T
        ent_counts = ent_counts[ent_counts[:, 1].argsort()]

        sample_size = int(np.sqrt(_max_candidates))

        sample_e_s = np.random.choice(ent_counts[0:max_candidates, 0], size=sample_size, replace=False)
        sample_e_o = np.random.choice(ent_counts[0:max_candidates, 0], size=sample_size, replace=False)

        X_candidates = np.array(np.meshgrid(sample_e_s, target_rel, sample_e_o)).T.reshape(-1, 3)
        X_candidates = _filter_candidates(X_candidates, X)

        yield _filter_candidates(X_candidates, X)

    elif strategy in ['graph_degree', 'cluster_coefficient',
                      'cluster_triangles', 'cluster_squares']:

        # Create networkx graph
        G = nx.Graph()
        for row in X:
            G.add_nodes_from([row[0], row[2]])
            G.add_edge(row[0], row[2], name=row[1])

        # Calculate node metrics
        if strategy == 'graph_degree':
            C = {i: j for i, j in G.degree()}
        elif strategy == 'cluster_coefficient':
            C = nx.algorithms.cluster.clustering(G)
        elif strategy == 'cluster_triangles':
            C = nx.algorithms.cluster.triangles(G)
        elif strategy == 'cluster_squares':
            C = nx.algorithms.cluster.square_clustering(G)

        # Convert to np.array and sort metric column in descending order
        C = np.array([[k, v] for k, v in C.items()])
        C = C[C[:, 1].argsort()]

        sample_size = int(np.sqrt(_max_candidates))

        sample_e_s = np.random.choice(C[0:max_candidates, 0], size=sample_size, replace=False)
        sample_e_o = np.random.choice(C[0:max_candidates, 0], size=sample_size, replace=False)

        X_candidates = np.array(np.meshgrid(sample_e_s, target_rel, sample_e_o)).T.reshape(-1, 3)
        X_candidates = _filter_candidates(X_candidates, X)

        yield X_candidates

    return


def _setdiff2d(A, B):
    """ Utility function equivalent to numpy.setdiff1d on 2d arrays.

    Parameters
    ----------

    A : ndarray, shape [n, m]

    B : ndarray, shape [n, m]

    Returns
    -------
    np.array, shape [k, m]
        Rows of A that are not in B.

    """

    if len(A.shape) != 2 or len(B.shape) != 2:
        raise RuntimeError('Input arrays must be 2-dimensional.')

    tmp = np.prod(np.swapaxes(A[:, :, None], 1, 2) == B, axis=2)
    return A[~ np.sum(np.cumsum(tmp, axis=0) * tmp == 1, axis=1).astype(bool)]


def find_clusters(X, model, clustering_algorithm=DBSCAN(),
                  entities_subset=None, relations_subset=None):
    """
    Perform link-based cluster analysis on a knowledge graph.

    Clustering is exclusive (i.e. a triple is assigned to one and only one
    cluster).

    Parameters
    ----------

    X : ndarray, shape [n, 3]
        The input knowledge graph (triples) to be clustered.
    model : EmbeddingModel
        The fitted model that will be used to generate the embeddings.
        This model must have been fully trained already, be it directly with
        `fit` or from a helper function such as `select_best_model_ranking`.
    clustering_algorithm : object
        The initialized object of the clustering algorithm.
        It should be ready to apply the `fit_predict` method.
        Please see:
         https://scikit-learn.org/stable/modules/clustering.html#clustering
        to understand the clustering API provided by scikit-learn.
        The default clustering model is sklearn's DBSCAN with its default
        parameters.
    entities_subset: ndarray, shape [n]
        The entities to consider for clustering. This is a subset of all the
        entities included in X.
        If None, all entities will be clustered.
        To exclude all relations from clustering, pass an empty array.
    relations_subset: ndarray, shape [n]
        The relation types to consider for clustering. This is a subset of
        all the relation types included in X.
        If None, all relations will be clustered.
        To exclude all relations from clustering, pass an empty array.

    Returns
    -------
    labels : ndarray, shape [n]
        Index of the cluster each triple belongs to.

    Examples
    --------
    >>> import requests
    >>> import pandas as pd
    >>> import numpy as np
    >>> from sklearn.manifold import TSNE
    >>> from sklearn.cluster import DBSCAN
    >>> import matplotlib.pyplot as plt
    >>> import seaborn as sns
    >>>
    >>> # adjustText lib: https://github.com/Phlya/adjustText
    >>> from adjustText import adjust_text
    >>>
    >>> from ampligraph.datasets import load_from_csv
    >>> from ampligraph.latent_features import ComplEx
    >>> from ampligraph.discovery import find_clusters
    >>>
    >>> # Game of Thrones relations dataset
    >>> url = 'https://ampligraph.s3-eu-west-1.amazonaws.com/datasets/GoT.csv'
    >>> open('GoT.csv', 'wb').write(requests.get(url).content)
    >>> X = load_from_csv('.', 'GoT.csv', sep=',')
    >>>
    >>> model = ComplEx(batches_count=10,
    >>>                 seed=0,
    >>>                 epochs=200,
    >>>                 k=150,
    >>>                 eta=5,
    >>>                 optimizer='adam',
    >>>                 optimizer_params={'lr':1e-3},
    >>>                 loss='multiclass_nll',
    >>>                 regularizer='LP',
    >>>                 regularizer_params={'p':3, 'lambda':1e-5},
    >>>                 verbose=True)
    >>> model.fit(X)
    >>>
    >>> # Find clusters of embeddings using DBSCAN
    >>> clusters = find_clusters(X, model, clustering_algorithm=DBSCAN(eps=10))
    >>>
    >>> # Get embeddings
    >>> s = model.get_embeddings(X[:, 0], embedding_type='entity')
    >>> p = model.get_embeddings(X[:, 1], embedding_type='relation')
    >>> o = model.get_embeddings(X[:, 2], embedding_type='entity')
    >>>
    >>> # Project embeddings into 2D space usint t-SNE
    >>> embeddings_2d = TSNE(n_components=2).fit_transform(np.hstack((s, p, o)))
    >>>
    >>> # Plot results
    >>> df = pd.DataFrame({"s": X[:, 0], "p": X[:, 1], "o": X[:, 2],
    >>>                    "embedding1": embeddings_2d[:, 0], "embedding2": embeddings_2d[:, 1], "clusters": clusters})
    >>>
    >>> plt.figure(figsize=(15, 15))
    >>> plt.title("Clustered embeddings")
    >>>
    >>> ax = sns.scatterplot(data=df.assign(clusters=df.clusters.apply(str)+'_'),
    >>>                      x="embedding1", y="embedding2", hue="clusters")
    >>>
    >>> texts = []
    >>>
    >>> for i, point in df.iterrows():
    >>>     if np.random.uniform() < 0.02:
    >>>         texts.append(plt.text(point['embedding1']+.02, point['embedding2'], str(point['p'])))
    >>>
    >>> adjust_text(texts)

    .. image:: ../../docs/img/clustering/clustered_embeddings_docstring.png

    """

    if not model.is_fitted:
        raise ValueError("Model has not been fitted.")

    if not hasattr(clustering_algorithm, "fit_predict"):
        raise ValueError("Clustering algorithm does not have the "
                         "`fit_predict` method.")

    s = model.get_embeddings(X[:, 0], embedding_type='entity')
    p = model.get_embeddings(X[:, 1], embedding_type='relation')
    o = model.get_embeddings(X[:, 2], embedding_type='entity')

    mask = np.ones(len(X), dtype=np.bool)

    if entities_subset is not None:
        if len(entities_subset) == 0:
            s = np.empty(p.shape)
            o = np.empty(p.shape)
        else:
            mask &= ~(np.isin(X[:, 0], entities_subset) | np.isin(X[:, 2], entities_subset))

    if relations_subset is not None:
        if len(relations_subset) == 0:
            p = np.empty(s.shape)
        else:
            mask &= ~np.isin(X[:, 1], relations_subset)

    X = np.hstack((s, p, o))[mask]

    return clustering_algorithm.fit_predict(X)
