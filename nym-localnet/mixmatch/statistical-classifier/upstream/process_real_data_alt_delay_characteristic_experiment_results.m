function process_real_data_alt_delay_characteristic_experiment_results(results_folder, database_id, client_subsets_count)
  database_folder_path = [results_folder '/' database_id];
  max_sequences_length = 500;
  lengths_vector = 10:10:max_sequences_length;
  color_codes = ['k','r','g','b','y','m','c'];
  line_style_codes{1} = '-';
  line_style_codes{2} = '--';

  sets{1} = 'validation';
  % sets{2} = 'test';

  close all;

  for set_index = 1:length(sets)
			    %  if ~exist('global_score_matrix', 'var')
    fprintf(1, 'Loading real data...\n');
    clear global_ack_mean_delay;
    clear global_ack_min_delay;
    clear global_data_mean_delay;
    clear global_ack_min_delay;
    clear global_score_matrix;
    for client_subset_index = 1:client_subsets_count
      fprintf(1, '\r\tProgress: %.5f%%... ', 100*(client_subset_index - 1)/client_subsets_count); fflush(1);
      % experiment_data_file_name = [database_folder_path '/real_data_experiment_alt_delay_characteristic_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '_' database_id '.oct'];
      experiment_data_file_name = [database_folder_path '/real_data_experiment_alt_delay_characteristic_' sets{set_index} '_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '_' database_id  '.oct'];
      load(experiment_data_file_name);
%      eval([sets{set_index} '_set_samples_count = length(' sets{set_index} '_set_folders_cell);']);
% assert(size(ack_mean_delay, 1) == experiments_count/client_subsets_count);
		     % if ~exist('global_ack_mean_delay', 'var')
		     %  global_ack_mean_delay = [];
		     %endif
		     %      if ~exist('global_ack_min_delay', 'var')
		     %	global_ack_min_delay = [];
		     %      endif
		     %      if ~exist('global_data_mean_delay', 'var')
		     %	global_data_mean_delay = [];
		     %      endif
		     %if ~exist('global_data_min_delay', 'var')
		     %  global_data_min_delay = [];
		     %endif
      if ~exist('global_score_matrix', 'var')
	global_score_matrix = [];
      endif
%eval(['global_ack_mean_delay = [global_ack_mean_delay; ack_mean_delay_' sets{set_index} '];']);
% eval(['global_ack_min_delay = [global_ack_min_delay; ack_min_delay_' sets{set_index} '];']);
% eval(['global_data_mean_delay = [global_data_mean_delay; data_mean_delay_' sets{set_index} '];']);
%eval(['global_data_min_delay = [global_data_min_delay; data_min_delay_' sets{set_index} '];']);
      eval(['global_score_matrix = [global_score_matrix; score_matrix_' sets{set_index} '];']);
      fprintf(1, '\r\tProgress: %.5f%% ... ', 100*client_subset_index/client_subsets_count); fflush(1);
    endfor
    eval(['clear ack_mean_delay_' sets{set_index} ';']);
    eval(['clear ack_min_delay_' sets{set_index} ';']);
    eval(['clear data_mean_delay_' sets{set_index} ';']);
    eval(['clear data_min_delay_' sets{set_index} ';']);
    eval(['clear score_matrix_' sets{set_index} ';']);
    fprintf(1, 'Done!\n');
    eval([sets{set_index} '_set_samples_count = size(global_score_matrix, 1);']);
    eval(['fprintf(1, ''%s set samples count: %i\n'', sets{set_index}, ' sets{set_index} '_set_samples_count);']);
				%  endif
    thr = zeros(length(lengths_vector) + 1, 1);
    FAR = zeros(length(lengths_vector) + 1, 1);
    FRR = zeros(length(lengths_vector) + 1, 1);
    EER = zeros(length(lengths_vector) + 1, 1);
    trivial_false_matches_count = zeros(length(lengths_vector) + 1, 1);
    trivial_true_matches_fail_count = zeros(length(lengths_vector) + 1, 1);
				% close all;
				% Prepare the DET figure
    figure(2*set_index - 1);
    title(['DET in ' sets{set_index} ' set']);
    hold on;
			      % Set_DET_limits(1e-5, 0.1, 1e-6, 2e-3);
    Set_DET_limits(1e-4, 0.4, 2e-4, 0.4);
				% Prepare the FPR vs TPR figure
    figure(2*set_index);
    title(['TPR vs FPR in ' sets{set_index} ' set']);
    hold on;
    eval(['axis([1/(' sets{set_index} '_set_samples_count*(' sets{set_index} '_set_samples_count - 1)) .25 -0.04 1.04]);']);
    eval(['min_exponent = floor(log10(1/(' sets{set_index} '_set_samples_count*(' sets{set_index} '_set_samples_count - 1))));']);
    max_exponent = ceil(log10(.25));
    exponent_interval = (max_exponent - min_exponent)/99;
    p = 10.^(min_exponent:exponent_interval:max_exponent);
    semilogx(p, p, 'b--;Random guess;');
    eval(['min_labeled_exponent = ceil(log10(1/(' sets{set_index} '_set_samples_count*(' sets{set_index} '_set_samples_count - 1))));']);
    max_labeled_exponent = floor(log10(.25));
    xtick_vals = 10.^(min_labeled_exponent:max_labeled_exponent);
    xtick_labels = {};
    for val_index = 1:length(xtick_vals)
      xtick_labels{val_index} = ['10^{' num2str(log10(xtick_vals(val_index))) '}'];
    endfor
    xticks(xtick_vals);
    xticklabels(xtick_labels);
    ylabel('TPR');
    xlabel('FPR');
    grid minor;
    plotted_curves_count = 0;
    eval(['experiments_count = ' sets{set_index} '_set_samples_count;']);
    for length_index = 1:length(lengths_vector)
      plot_DET_curve = (mod(lengths_vector(length_index), 100) == 0);
      if lengths_vector(length_index) > 700
	plot_DET_curve = 0;
      endif
      if plot_DET_curve
	plotted_curves_count = plotted_curves_count + 1;
      endif
      sequences_length = lengths_vector(length_index);
      fprintf(1, 'Analysing results using sequences of length %i...\n', sequences_length);
      fprintf(1, '\tReading true scores from score matrix... '); fflush(1);
      true_matches_scores = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index));
      fprintf(1, 'Done.\n');
      fprintf(1, '\tReading false scores from score matrix... '); fflush(1);
      false_matches_scores = zeros(experiments_count*(experiments_count - 1), 1);
      added_false_matches_scores = 0;
      for diag_index = 1:(experiments_count - 1)
	false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index), -diag_index);
	scores_count = length(false_matches_scores_diag);
	false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
	added_false_matches_scores = added_false_matches_scores + scores_count;
	false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index), diag_index);
	scores_count = length(false_matches_scores_diag);
	false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
	added_false_matches_scores = added_false_matches_scores + scores_count;
      endfor
      assert(added_false_matches_scores == experiments_count*(experiments_count - 1));
      fprintf(1, 'Done.\n');
      trivial_false_matches_count(length_index) = sum(false_matches_scores == -Inf);
      trivial_true_matches_fail_count(length_index) = sum(true_matches_scores == -Inf);
      trivial_true_matches_fail_locations{length_index} = find(true_matches_scores == -Inf);
      false_matches_count = length(false_matches_scores);
      true_matches_count = length(true_matches_scores);
      fprintf(1, '\tPerformance:\n');
      fprintf(1, '\t\tTrivial false matches rate:     \t%.5f%%\n', 100*trivial_false_matches_count(length_index)/false_matches_count);
      fprintf(1, '\t\tTrivial true matches fail rate: \t%.5f%%\n', 100*trivial_true_matches_fail_count(length_index)/true_matches_count);
      false_matches_scores(false_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
      true_matches_scores(true_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
      [thr(length_index), FAR(length_index), FRR(length_index), EER(length_index)] = getThresholdEER(true_matches_scores, false_matches_scores);
      fprintf(1, '\t\tFalse Alarms Rate @EER:         \t%.5f%%\n', 100*FAR(length_index));
      fprintf(1, '\t\tFalse Miss Rate @EER:           \t%.5f%%\n', 100*FRR(length_index));
      fprintf(1, '\t\tEqual Error Rate (EER):         \t%.5f%%\n', 100*EER(length_index));
      if plot_DET_curve
				% First, we plot the DET curve
	figure(2*set_index - 1);
	[Pmiss, Pfa] = Compute_DET(true_matches_scores, false_matches_scores);
	line_color = color_codes(mod(plotted_curves_count - 1, length(color_codes)) + 1);
	line_style = line_style_codes{mod(int32(floor((plotted_curves_count - 1)/length(color_codes))), 2) + 1};
	Plot_DET(Pmiss, Pfa, ['2' line_color line_style ';Sequences length = ' num2str(sequences_length) ';']);
			    % Then, we plot the FPR vs TPR in semilogx
			    % Pmiss = FNR = 1 - TPR; Pfa = FPR, so...
	figure(2*set_index);
	semilogx(Pfa, 1 - Pmiss, ['2' line_color line_style ';Sequences length = ' num2str(sequences_length) ';']);
	save([results_folder '/' database_id '_' sets{set_index} '_Pmiss_Pfa_' num2str(sequences_length) '.oct'], 'Pmiss', 'Pfa');
      endif
    endfor
    fprintf(1, 'Analysing results using full sequences...\n');
    fprintf(1, '\tReading true scores from score matrix... '); fflush(1);
    true_matches_scores = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end));
    fprintf(1, 'Done.\n');
    fprintf(1, '\tReading false scores from score matrix... '); fflush(1);
    false_matches_scores = zeros(experiments_count*(experiments_count - 1), 1);
    added_false_matches_scores = 0;
    for diag_index = 1:(experiments_count - 1)
      false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end), -diag_index);
      scores_count = length(false_matches_scores_diag);
      false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
      added_false_matches_scores = added_false_matches_scores + scores_count;
      false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end), diag_index);
      scores_count = length(false_matches_scores_diag);
      false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
      added_false_matches_scores = added_false_matches_scores + scores_count;
    endfor
    assert(added_false_matches_scores == experiments_count*(experiments_count - 1));
    fprintf(1, 'Done.\n');
    trivial_false_matches_count(end) = sum(false_matches_scores == -Inf);
    trivial_true_matches_fail_count(end) = sum(true_matches_scores == -Inf);
    trivial_true_matches_fail_locations{length(lengths_vector) + 1} = find(true_matches_scores == -Inf);
    false_matches_count = length(false_matches_scores);
    true_matches_count = length(true_matches_scores);
    fprintf(1, '\tPerformance:\n');
    fprintf(1, '\t\tTrivial false matches rate:     \t%.5f%%\n', 100*trivial_false_matches_count(end)/false_matches_count);
    fprintf(1, '\t\tTrivial true matches fail rate: \t%.5f%%\n', 100*trivial_true_matches_fail_count(end)/true_matches_count);
    false_matches_scores(false_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
    true_matches_scores(true_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
    [thr(end), FAR(end), FRR(end), EER(end)] = getThresholdEER(true_matches_scores, false_matches_scores);
    fprintf(1, '\t\tFalse Alarms Rate @EER:       \t%.5f%%\n', 100*FAR(end));
    fprintf(1, '\t\tFalse Miss Rate @EER:         \t%.5f%%\n', 100*FRR(end));
    fprintf(1, '\t\tEqual Error Rate (EER):       \t%.5f%%\n', 100*EER(end));
    plotted_curves_count = plotted_curves_count + 1;
    [Pmiss, Pfa] = Compute_DET(true_matches_scores, false_matches_scores);
    line_color = color_codes(mod(plotted_curves_count - 1, length(color_codes)) + 1);
    line_style = line_style_codes{mod(int32(floor((plotted_curves_count - 1)/length(color_codes))), 2) + 1};
    figure(2*set_index - 1);
    Plot_DET(Pmiss, Pfa, ['2' line_color line_style ';Full sequences;']);
    legend('location', 'eastoutside');
			    % Then, we plot the FPR vs TPR in semilogx
			    % Pmiss = FNR = 1 - TPR; Pfa = FPR, so...
    figure(2*set_index);
    semilogx(Pfa, 1 - Pmiss, ['2' line_color line_style ';Full sequences;']);
    legend('location', 'eastoutside');
    title('Detection performance of statistical approach using Gaussian delay characteristic');
    save([results_folder '/' database_id '_' sets{set_index} '_Pmiss_Pfa_full.oct'], 'Pmiss', 'Pfa');
  endfor

endfunction
