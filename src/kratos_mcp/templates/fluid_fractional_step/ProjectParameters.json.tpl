{
    "problem_data": {
        "problem_name": "{{problem_name}}",
        "parallel_type": "OpenMP",
        "echo_level": 1,
        "start_time": 0.0,
        "end_time": "{{end_time}}"
    },
    "solver_settings": {
        "solver_type": "FractionalStep",
        "model_part_name": "FluidModelPart",
        "domain_size": "{{domain_size}}",
        "echo_level": 1,
        "model_import_settings": {
            "input_type": "mdpa",
            "input_filename": "{{mdpa_basename}}"
        },
        "material_import_settings": {
            "materials_filename": "{{materials_filename}}"
        },
        "predictor_corrector": false,
        "maximum_velocity_iterations": 3,
        "maximum_pressure_iterations": 3,
        "velocity_tolerance": 1e-3,
        "pressure_tolerance": 1e-2,
        "dynamic_tau": 0.01,
        "compute_reactions": false,
        "reform_dofs_at_each_step": false,
        "velocity_linear_solver_settings": {
            "solver_type": "bicgstab",
            "max_iteration": 5000,
            "tolerance": 1e-6,
            "preconditioner_type": "ilu0"
        },
        "pressure_linear_solver_settings": {
            "solver_type": "amgcl",
            "max_iteration": 200,
            "tolerance": 1e-6,
            "krylov_type": "cg",
            "coarsening_type": "aggregation",
            "smoother_type": "ilu0"
        },
        "volume_model_part_name": "{{volume_part}}",
        "skin_parts": "{{skin_parts}}",
        "no_skin_parts": [],
        "time_stepping": {
            "automatic_time_step": false,
            "time_step": "{{time_step}}"
        },
        "formulation": {
            "element_type": "FractionalStep",
            "condition_type": "WallCondition"
        }
    },
    "processes": {
        "initial_conditions_process_list": [],
        "boundary_conditions_process_list": [{
            "python_module": "assign_vector_variable_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "AssignVectorVariableProcess",
            "Parameters": {
                "model_part_name": "{{inlet_model_part}}",
                "variable_name": "VELOCITY",
                "value": "{{inlet_velocity}}",
                "constrained": [true, true, true],
                "interval": [0.0, "End"]
            }
        }, {
            "python_module": "assign_scalar_variable_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "AssignScalarVariableProcess",
            "Parameters": {
                "model_part_name": "{{outlet_model_part}}",
                "variable_name": "PRESSURE",
                "value": 0.0,
                "constrained": true,
                "interval": [0.0, "End"]
            }
        }],
        "gravity": [],
        "auxiliar_process_list": []
    },
    "output_processes": {
        "gid_output": [],
        "vtk_output": [{
            "python_module": "vtk_output_process",
            "kratos_module": "KratosMultiphysics",
            "process_name": "VtkOutputProcess",
            "Parameters": {
                "model_part_name": "FluidModelPart",
                "output_control_type": "step",
                "output_interval": 1,
                "file_format": "ascii",
                "output_precision": 7,
                "output_sub_model_parts": false,
                "output_path": "{{output_path}}",
                "save_output_files_in_folder": true,
                "nodal_solution_step_data_variables": "{{nodal_results}}",
                "nodal_data_value_variables": [],
                "element_data_value_variables": [],
                "condition_data_value_variables": []
            }
        }]
    },
    "analysis_stage": "KratosMultiphysics.FluidDynamicsApplication.fluid_dynamics_analysis"
}
